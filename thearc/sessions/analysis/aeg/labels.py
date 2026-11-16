"""Step 1: semantic normalisation, every event mapped to a small fixed vocabulary of action labels.

Rules (command prefixes, tool names, file extensions, phrasing) label everything deterministically. Free text — what a
user message asks for and what an assistant message does — can be labelled by an LLM instead (``LLMLabeler``): strings
are deduplicated, answers cached on disk, and anything the model does not answer falls back to the rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from .events import Case, CaseEvent

USER_INTENTS = ("TASK_NEW", "CORRECTION", "CLARIFY", "APPROVE", "CONTINUE", "ABORT", "QUESTION")
ASSISTANT_KINDS = ("PLAN", "EXPLAIN", "ASK_USER", "FINAL_SUMMARY")


@dataclass
class Action:
    """A labelled step: a tool call with its result, a message, or a marker such as a compaction."""

    label: str  # e.g. TEST:fail, EDIT:src, USER:CORRECTION, PLAN
    family: str  # The label's head: TEST, EDIT, USER, …
    outcome: str | None = None  # ok, fail
    start: str | None = None
    end: str | None = None
    event_ids: list[str] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    sample: str = ""
    paths: tuple[str, ...] = ()
    tool: str | None = None
    model: str | None = None


# --- files and commands ------------------------------------------------------------------------------------------

_DOC = {".md", ".rst", ".txt", ".adoc", ".html"}
_CONFIG = {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".lock", ".env", ".xml", ".properties"}
_CONFIG_NAMES = {"dockerfile", "makefile", "justfile", ".gitignore", "pyproject.toml", "package.json", "setup.cfg"}
_CODE = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp",
         ".rb", ".php", ".swift", ".scala", ".cs", ".sh", ".sql", ".vue", ".svelte", ".lua", ".ex", ".exs"}  # fmt: skip


def file_kind(path: str) -> str:
    """``test``, ``src``, ``config``, ``docs`` or ``other`` for a file path."""
    pure = PurePosixPath(path.strip().strip("'\""))
    name, suffix = pure.name.lower(), pure.suffix.lower()
    parts = {part.lower() for part in pure.parts}
    if name.startswith("test_") or name.endswith(("_test" + suffix, ".test" + suffix, ".spec" + suffix)) \
            or parts & {"tests", "test", "__tests__", "spec"}:  # fmt: skip
        return "test"
    if name in _CONFIG_NAMES or suffix in _CONFIG or (name.startswith(".") and not suffix):
        return "config"
    if suffix in _DOC:
        return "docs"
    if suffix in _CODE:
        return "src"
    return "other"


_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "test",
        re.compile(
            r"^(pytest|py\.test|tox|nox|jest|vitest|mocha|rspec|phpunit|ctest|"
            r"(python3?|uv run( python3?)?) -m (pytest|unittest)|go test|cargo test|"
            r"(npm|yarn|pnpm|bun)( run)? test|make (test|check))\b"
        ),
    ),
    (
        "install",
        re.compile(
            r"^((pip3?|uv pip) install|uv (add|sync)|(npm|pnpm) (install|i|ci)\b|yarn (add|install)|"
            r"poetry (add|install)|brew install|apt(-get)? install|cargo add|go get|gem install)"
        ),
    ),
    (
        "build",
        re.compile(
            r"^(make|cmake|cargo (build|check|clippy)|go (build|vet)|tsc|mvn|gradle|"
            r"(npm|yarn|pnpm|bun)( run)? (build|lint|typecheck)|ruff|mypy|pyright|eslint|black|"
            r"prettier|flake8|pylint|docker build|python3? -m build)\b"
        ),
    ),
    ("edit", re.compile(r"^(sed -i|sed -E -i|perl -p?i|tee\b|patch\b|apply_patch\b)")),
    ("git", re.compile(r"^(git|gh)\b")),
    (
        "search",
        re.compile(
            r"^(grep|rg|ag|ack|find|fd|ls|tree|cat|head|tail|sed -n|wc|less|more|file|stat|du|"
            r"awk|graphify|bat|nl|pwd|which|jq)\b"
        ),
    ),
    (
        "run",
        re.compile(
            r"^(python3?|uv run|node|deno|bun|npx|\./|bash|sh|zsh|ruby|go run|cargo run|"
            r"(npm|yarn|pnpm) (start|run)|curl|wget|docker|sqlite3|thearc)\b"
        ),
    ),
]
_PRIORITY = ("test", "build", "install", "edit", "git", "run", "search", "other")
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_PREFIX = re.compile(r"^((cd|export|source|set|ulimit)\b[^&;|]*|[A-Z_][A-Z0-9_]*=\S*\s+|timeout\s+\d+\s+|time\s+|"
                     r"env\s+(\S+=\S+\s+)*)")  # fmt: skip


def _parts(command: str) -> list[tuple[str, str]]:
    """Each command of a script with its family, setup prefixes (cd, export, env assignments) removed."""
    found = []
    for part in _SPLIT.split(command.strip()):
        part = part.strip().lstrip("(").strip()
        while part and (match := _PREFIX.match(part)) and match.end() > 0:
            part = part[match.end() :].strip()
        if part:
            found.append((next((name for name, pattern in _FAMILIES if pattern.match(part)), "other"), part))
    return found


def significant_part(command: str) -> str:
    """The command of a script that decides its family: ``pytest -q`` in ``cd app && grep x f; pytest -q``."""
    parts = _parts(command)
    family = command_family(command)
    return next((part for name, part in parts if name == family), command.strip())


def command_family(command: str) -> str:
    """The most significant family among a script's commands: test > build > install > edit > git > run > search >
    other."""
    found = {family for family, _ in _parts(command)}
    return next((family for family in _PRIORITY if family in found), "other") if found else "other"


def _command_paths(command: str) -> list[str]:
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    return [word for word in words if "/" in word or re.search(r"\.\w{1,5}$", word)][:5]


def _target(paths: Sequence[str]) -> str:
    kinds = [file_kind(path) for path in paths]
    for kind in ("test", "src", "config", "docs"):
        if kind in kinds:
            return kind
    return "other" if kinds else "code"


def call_label(event: CaseEvent) -> str:
    """The label of a tool call, before its outcome: EXPLORE:src, EDIT:test, TEST, GIT, DELEGATE, …"""
    action, tool = (event.action_kind or ""), (event.tool_name or "").lower().rsplit("__", 1)[-1]
    if event.command is not None:
        family = command_family(event.command)
        if family == "search":
            return f"EXPLORE:{_target(event.paths or _command_paths(event.command))}"
        if family == "edit":
            return f"EDIT:{_target(event.paths or _command_paths(event.command))}"
        return {"test": "TEST", "build": "BUILD", "install": "INSTALL", "git": "GIT", "run": "RUN"}.get(family, "SHELL")
    if action.startswith(("file.read", "search.")) or tool in {"read", "grep", "glob", "ls", "list_dir", "find",
                                                                "view", "search", "codebase_search"}:  # fmt: skip
        return f"EXPLORE:{_target(event.paths)}"
    if action.startswith(("file.write", "file.patch")) or tool in {"edit", "write", "multiedit", "apply_patch",
                                                                    "notebookedit", "str_replace_editor"}:  # fmt: skip
        return f"EDIT:{_target(event.paths) if event.paths else 'src'}"
    if action.startswith("agent.") or tool in {"task", "agent", "spawn_agent"}:
        return "DELEGATE"
    if tool in {"todowrite", "update_plan", "exitplanmode", "enterplanmode"}:
        return "PLAN"
    if tool in {"askuserquestion", "ask_user", "request_user_input"}:
        return "ASK_USER"
    if tool in {"webfetch", "websearch", "web_search", "fetch", "browse"}:
        return "WEB"
    if tool == "skill":
        return "SKILL"
    if (event.tool_name or "").startswith("mcp__"):
        return "TOOL:mcp"
    return "TOOL"


# --- free text ---------------------------------------------------------------------------------------------------

_META = re.compile(r"^\s*(<(command-|local-command|system-reminder|environment_context|user_instructions|"
                   r"recommended_plugins|app-context|permissions)|caveat:|# agents\.md|base directory for this skill)",
                   re.IGNORECASE)  # fmt: skip
_ABORT = re.compile(r"^\s*(\[request interrupted|stop\b|abort\b|cancel\b|never ?mind\b|forget it\b)", re.IGNORECASE)
_CORRECTION = re.compile(
    r"^\s*(no\b|nope\b|wrong\b|that'?s not|that is not|not what|don'?t\b|do not\b|instead\b|actually\b|revert\b|"
    r"undo\b|why did you|you (should|shouldn'?t|didn'?t|forgot)|wait\b|hmm\b)|\binstead of\b",
    re.IGNORECASE,
)
_APPROVE = re.compile(r"^\s*(ok(ay)?|yes|yep|yeah|good|great|perfect|lgtm|looks good|thanks?|thank you|nice|"
                      r"go ahead|do it|proceed|ship it|approved?|sure|correct|exactly)\b", re.IGNORECASE)  # fmt: skip
_CONTINUE = re.compile(r"^\s*(continue|go on|keep going|next|resume|carry on)\b", re.IGNORECASE)
_TASK = re.compile(r"^\s*(add|implement|create|make|write|build|fix|refactor|change|update|remove|delete|rename|"
                   r"let'?s|lets|can (we|you)|could (we|you)|please|i (want|would like|need)|now)\b",
                   re.IGNORECASE)  # fmt: skip
_QUESTION = re.compile(r"^\s*(what|why|how|where|which|who|when|is|are|does|do|can|could|should|will)\b",
                       re.IGNORECASE)  # fmt: skip
_PLAN = re.compile(r"^\s*(i'?ll\b|i will\b|let me\b|let's\b|plan\b|first[,:]|next[,:]|now\b|step \d|1[.)]\s|"
                   r"i'?m going to|going to)", re.IGNORECASE)  # fmt: skip


def is_meta(text: str) -> bool:
    """Harness-injected text recorded as a user message (command output, reminders, environment context)."""
    return bool(_META.match(text))


def user_intent(text: str, *, first: bool) -> str:
    """A user message's intent by rules: the first one is the task; then aborts, corrections, approvals, …"""
    if _ABORT.match(text):
        return "ABORT"
    if first:
        return "TASK_NEW"
    short = len(text) <= 160
    if _CORRECTION.search(text[:200]):
        return "CORRECTION"
    if short and _CONTINUE.match(text):
        return "CONTINUE"
    if short and _APPROVE.match(text) and not _TASK.search(text[20:120]):
        return "APPROVE"
    if _TASK.match(text) or len(text) > 400:
        return "TASK_NEW"
    if text.rstrip().endswith("?") or (_QUESTION.match(text) and len(text) < 300):
        return "QUESTION"
    return "CLARIFY"


def assistant_kind(text: str, *, last: bool) -> str:
    """What an assistant message does: the case's last one is its final summary; then questions, plans, the rest."""
    if last:
        return "FINAL_SUMMARY"
    if "?" in text[-200:]:
        return "ASK_USER"
    if _PLAN.match(text):
        return "PLAN"
    return "EXPLAIN"


class LLMLabeler:
    """Labels free text with a model, deduplicated and cached: ``complete(prompt) -> answer`` makes the call.

    The default ``complete`` runs ``claude -p`` with a small model; any callable that answers a prompt works.
    Answers are a JSON object mapping each item's number to one label of the fixed taxonomy.
    """

    def __init__(self, complete: Callable[[str], str] | None = None, *, cache: Path | None = None,
                 batch: int = 40, model: str = "claude-haiku-5-5") -> None:  # fmt: skip
        self.complete = complete or self._claude
        self.cache_path, self.batch, self.model = cache, batch, model
        self.cache: dict[str, str] = {}
        self.calls = 0
        self.failures: list[str] = []
        if cache is not None and cache.exists():
            self.cache = json.loads(cache.read_text(encoding="utf-8"))

    def _claude(self, prompt: str) -> str:
        completed = subprocess.run(["claude", "-p", "--model", self.model], input=prompt, capture_output=True,
                                   text=True, timeout=300, check=False)  # fmt: skip
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"claude exited with {completed.returncode}")
        return completed.stdout

    @staticmethod
    def _key(kind: str, text: str) -> str:
        return hashlib.sha256(f"{kind}\0{text}".encode()).hexdigest()[:24]

    def label(self, kind: str, texts: Sequence[str]) -> dict[str, str]:
        """Labels for ``texts`` (``kind`` is ``user`` or ``assistant``); texts it could not label are left out."""
        taxonomy = USER_INTENTS if kind == "user" else ASSISTANT_KINDS
        unique = list(dict.fromkeys(texts))
        todo = [text for text in unique if self._key(kind, text) not in self.cache]
        for start in range(0, len(todo), self.batch):
            chunk = todo[start : start + self.batch]
            items = "\n".join(f"{number}. {json.dumps(text[:600], ensure_ascii=False)}"
                              for number, text in enumerate(chunk, 1))  # fmt: skip
            what = "user messages to a coding agent: what each one asks for" if kind == "user" else \
                "coding-agent messages to the user: what each one does"  # fmt: skip
            prompt = (f"Classify these {what}.\nLabels: {', '.join(taxonomy)}.\n"
                      "Answer with only a JSON object mapping each item number to its label.\n\n" + items)  # fmt: skip
            try:
                self.calls += 1
                answer = self.complete(prompt)
                match = re.search(r"\{.*\}", answer, re.DOTALL)
                parsed = json.loads(match.group(0)) if match else {}
            except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
                self.failures.append(str(error)[:200])
                continue
            for number, text in enumerate(chunk, 1):
                value = str(parsed.get(str(number), "")).upper()
                if value in taxonomy:
                    self.cache[self._key(kind, text)] = value
        if self.cache_path is not None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=0, sort_keys=True), encoding="utf-8")
        return {text: self.cache[self._key(kind, text)] for text in unique if self._key(kind, text) in self.cache}


# --- labelling a case --------------------------------------------------------------------------------------------


def label_case(case: Case, *, user_labels: dict[str, str] | None = None,
               assistant_labels: dict[str, str] | None = None) -> list[Action]:  # fmt: skip
    """The case's actions in order: each tool call joined with its result's outcome, messages labelled, usage
    attributed to the step it was spent on."""
    actions: list[Action] = []
    by_call: dict[str, Action] = {}
    events = case.events
    texts = [i for i, event in enumerate(events) if event.kind == "assistant_text" and event.text.strip()]
    last_text = texts[-1] if texts and not any(e.kind == "tool_call" for e in events[texts[-1] + 1 :]) else None
    first_user = True
    pending_tokens, pending_cost = 0, 0.0

    def add(action: Action, event: CaseEvent) -> Action:
        nonlocal pending_tokens, pending_cost
        action.start = action.end = event.timestamp
        action.event_ids.append(event.event_id)
        action.tokens += event.tokens_in + event.tokens_out + pending_tokens
        action.cost += event.cost + pending_cost
        action.model = event.model
        pending_tokens, pending_cost = 0, 0.0
        actions.append(action)
        return action

    for index, event in enumerate(events):
        if event.kind == "usage":  # Codex reports usage after the reply it paid for.
            if actions:
                actions[-1].tokens += event.tokens_in + event.tokens_out
                actions[-1].cost += event.cost
            else:
                pending_tokens += event.tokens_in + event.tokens_out
                pending_cost += event.cost
        elif event.kind == "user_msg":
            text = event.text.strip()
            if not text or is_meta(text):
                continue
            if actions and actions[-1].family == "USER" and actions[-1].sample == text[:160]:
                continue  # The same prompt recorded twice in a row.
            intent = (user_labels or {}).get(text) or user_intent(text, first=first_user)
            first_user = False
            add(Action(f"USER:{intent}", "USER", sample=text[:160]), event)
        elif event.kind == "assistant_text":
            text = event.text.strip()
            if not text:
                continue
            kind = (assistant_labels or {}).get(text) or assistant_kind(text, last=index == last_text)
            if index == last_text:
                kind = "FINAL_SUMMARY"
            add(Action(kind, kind, sample=text[:160]), event)
        elif event.kind == "thinking":
            add(Action("THINK", "THINK"), event)
        elif event.kind == "tool_call":
            if event.call_id and event.call_id in by_call:
                continue  # The same call recorded twice (Claude rewrites streamed messages): count it once.
            label = call_label(event)
            if event.command:
                event = replace(event, args_summary=significant_part(event.command)[:200])
            action = add(Action(label, label.split(":")[0], sample=event.args_summary[:160], paths=event.paths,
                                tool=event.tool_name), event)  # fmt: skip
            if event.call_id:
                by_call[event.call_id] = action
        elif event.kind == "tool_result":
            paired = by_call.get(event.call_id or "")
            if paired is None:
                continue
            paired.outcome = event.result_status
            paired.end = event.timestamp or paired.end
            paired.event_ids.append(event.event_id)
            if event.result_status == "fail" and event.error_text:
                paired.sample = f"{paired.sample} ⟶ {event.error_text.splitlines()[0][:100]}"
        elif event.kind in {"compaction", "model_change", "api_error"}:
            name = {"compaction": "COMPACT", "model_change": "MODEL", "api_error": "API_ERROR"}[event.kind]
            shown = (event.text or event.model) if name == "API_ERROR" else (event.model or event.text)
            add(Action(name, name, sample=(shown or "")[:160]), event)
    for action in actions:
        if action.outcome is not None and action.family not in {"USER", "THINK", "MODEL", "COMPACT"}:
            # Exploration and edits mostly succeed; only their failures are worth a label of their own.
            if action.family in {"EXPLORE", "EDIT"}:
                action.label = f"{action.label}:fail" if action.outcome == "fail" else action.label
            else:
                action.label = f"{action.label}:{action.outcome}"
    return actions
