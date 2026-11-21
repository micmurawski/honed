"""Harness runtimes: start or fork a reflector session and run its turn.

A runtime opens a **conversation** in a working directory that holds the evaluated
agent's files, with the ``submit_reflection`` MCP tool connected for that run only;
``send`` delivers one message and returns the reflector's final answer for that turn; ``usage`` adds up
the tokens (and, where the harness reports it, the cost) of every turn sent.
Reflectors may only read and submit: each runtime restricts the harness to its read-only
tools plus the submit tool, and turns off the user's own customizations (hooks, skills,
context files) where the harness allows it.

| Harness | How | Submit tool | Handoff (fork) |
| --- | --- | --- | --- |
| codex | ``codex exec --json``, read-only sandbox | config override | yes (``codex exec fork``) |
| claude | ``claude -p``, Read/Grep/Glob only | ``--mcp-config`` | yes (``--resume … --fork-session``) |
| pi | ``pi -p --mode json``, read/grep/find/ls only | ``-e`` extension | yes (``--fork``) |
| opencode | ``opencode run --format json``, per-run permissions | remote MCP config | yes (import and fork) |
| antigravity | — | no per-run MCP servers | no |
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from honed.sessions import InstalledSession


class ReflectionError(RuntimeError):
    """A reflection could not be completed; ``code`` classifies why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


SERVER = "honed"
TOOL = "submit_reflection"


@dataclass(frozen=True)
class ToolEndpoint:
    """Where a runtime finds honed's tools for one run: an MCP server over streamable HTTP on localhost."""

    url: str
    tools: tuple[str, ...] = (TOOL,)

    @property
    def qualified(self) -> tuple[str, ...]:
        """The tools' names as Claude and Pi expose them."""
        return tuple(f"mcp__{SERVER}__{tool}" for tool in self.tools)


@dataclass(frozen=True)
class Usage:
    """Tokens an agent run used, as its harness reports them; ``cost`` in USD where the harness reports it.

    ``input`` counts uncached input tokens only, so ``input + cache_read + cache_write + output`` is every token
    processed whichever harness reported it. ``reasoning`` is the part of ``output`` spent reasoning, where known.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    cost: float | None = None

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def __add__(self, other: Usage) -> Usage:
        cost = None if self.cost is None and other.cost is None else (self.cost or 0) + (other.cost or 0)
        return Usage(self.input + other.input, self.output + other.output, self.cache_read + other.cache_read,
                     self.cache_write + other.cache_write, self.reasoning + other.reasoning, cost)

    def __bool__(self) -> bool:
        return bool(self.total or self.cost)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready, without a cost the harness did not report."""
        return {key: value for key, value in asdict(self).items() if not (key == "cost" and value is None)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Usage:
        names = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in (data or {}).items() if key in names})


class Conversation(ABC):
    """One reflector session. ``id`` is the harness's own session (or thread / conversation) ID; ``usage`` what its
    turns have used so far."""

    id: str | None = None
    usage: Usage = Usage()

    @abstractmethod
    def send(self, message: str) -> str:
        """Deliver ``message`` and return the reflector's final answer for that turn."""


class Runtime(ABC):
    """Runs reflector sessions in one harness. ``timeout`` bounds each turn, in seconds: a turn that stalls (a hung
    API call, a harness waiting on input) fails with a ``timeout`` error instead of holding the round."""

    harness: str
    can_fork: bool = True

    def __init__(self, *, model: str | None = None, effort: str | None = None, timeout: float = 900):
        self.model, self.effort, self.timeout = model, effort, timeout

    @abstractmethod
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> AbstractContextManager[Conversation]:
        """Open a session in ``workspace`` with ``tool`` connected; with ``fork``, a fork of an installed session.

        Sessions only read files, unless ``edit``: then they may also edit files inside ``workspace``
        (never run commands — anything else goes through honed's tools).
        """


# Set by a harness inside its own sessions. A runtime started from one (e.g. by /evolve) must not inherit them, or the
# harness takes it for a nested session of the user's.
NESTING = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def _run(command: list[str], *, cwd: Path, timeout: float, stdin: str | None = None,
        env_overrides: dict[str, str] | None = None) -> str:
    executable = shutil.which(command[0])
    if executable is None:
        raise ReflectionError("missing_runtime", f"{command[0]} is not installed or not on PATH")
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=cwd,
            env={**{name: value for name, value in os.environ.items() if name not in NESTING}, **(env_overrides or {})},
            input=stdin or "",  # Always close stdin: Pi otherwise waits for piped input on ours.
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReflectionError("timeout", f"{command[0]} did not finish within {timeout:.0f}s") from exc
    if result.returncode != 0 and not result.stdout.strip():
        raise ReflectionError("runtime_failed", f"{command[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout


# --- Codex -------------------------------------------------------------------------------------------------------

# Configuration overrides for the reflector's own run only; no user configuration file is edited.
_CODEX_OVERRIDES = (
    "features.hooks=false",
    "features.plugins=false",
    "features.apps=false",
    "features.memories=false",
    "features.multi_agent=false",
    "features.skip_host_skill_discovery=true",
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "project_doc_max_bytes=0",
    'web_search="disabled"',
    "notify=[]",
)


class CodexRuntime(Runtime):
    harness = "codex"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        if fork is not None and not Path(fork.path).is_file():
            raise ReflectionError("missing_history", f"Installed Codex session not found: {fork.path}")
        yield _CodexConversation(self, workspace, tool, fork, edit)


class _CodexConversation(Conversation):
    """``codex exec``: the first turn starts a thread (or forks the handed-off one), later turns resume it."""

    def __init__(
        self, runtime: CodexRuntime, workspace: Path, tool: ToolEndpoint, fork: InstalledSession | None, edit: bool
    ):
        self.runtime, self.workspace, self.tool, self.fork, self.edit = runtime, workspace, tool, fork, edit
        # Codex reports the thread's running total, and a fork's starts from its parent's: a turn used the difference.
        self._total = _codex_thread_total(Path(fork.path)) if fork is not None else Usage()

    def send(self, message: str) -> str:
        if self.id is not None:
            command = ["codex", "exec", "resume", self.id]
        elif self.fork is not None:
            command = ["codex", "exec", "fork", self.fork.session_id]
        else:
            command = ["codex", "exec"]
        command += ["-", "--json", "--skip-git-repo-check"]  # The message comes on stdin.
        overrides = (
            *_CODEX_OVERRIDES,
            f'mcp_servers.{SERVER}.url="{self.tool.url}"',
            f'mcp_servers.{SERVER}.default_tools_approval_mode="approve"',
            'approval_policy="never"',
            f'sandbox_mode="{"workspace-write" if self.edit else "read-only"}"',  # resume and fork take no -s
            *([f'model_reasoning_effort="{self.runtime.effort}"'] if self.runtime.effort else []),
        )
        for override in overrides:
            command += ["-c", override]
        command += ["-m", self.runtime.model] if self.runtime.model else []
        output = _run(command, cwd=self.workspace, timeout=self.runtime.timeout, stdin=message)
        answer, failure = None, None
        for line in output.split("\n"):  # JSON Lines: a string may hold U+2028, which splitlines() cuts at.
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "thread.started":
                self.id = event.get("thread_id") or self.id
            elif kind == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                answer = event["item"].get("text")
            elif kind == "turn.completed":
                total = _codex_usage(event.get("usage") or {})
                self.usage += Usage(*(getattr(total, name) - getattr(self._total, name)
                                      for name in ("input", "output", "cache_read", "cache_write", "reasoning")))
                self._total = total
            elif kind == "turn.failed":
                failure = str((event.get("error") or {}).get("message") or "failed")
        if failure is not None or not answer:
            raise ReflectionError("incomplete", f"Codex turn failed: {(failure or 'no answer')[:300]}")
        return str(answer)


def _codex_usage(raw: dict[str, Any]) -> Usage:
    cached = raw.get("cached_input_tokens") or 0
    return Usage(input=(raw.get("input_tokens") or 0) - cached, output=raw.get("output_tokens") or 0,
                 cache_read=cached, cache_write=raw.get("cache_write_input_tokens") or 0,
                 reasoning=raw.get("reasoning_output_tokens") or 0)


def _codex_thread_total(rollout: Path) -> Usage:
    """A recorded Codex thread's running token total, from its last ``token_count`` event: what a fork starts at."""
    total = Usage()
    for line in rollout.read_text(encoding="utf-8").split("\n"):
        try:
            payload = json.loads(line).get("payload") or {}
        except (ValueError, AttributeError):
            continue
        info = payload.get("info") if isinstance(payload, dict) and payload.get("type") == "token_count" else None
        if info and info.get("total_token_usage"):
            total = _codex_usage(info["total_token_usage"])
    return total


# --- Claude Code -------------------------------------------------------------------------------------------------


class ClaudeRuntime(Runtime):
    harness = "claude"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        if fork is not None and not Path(fork.path).is_file():
            raise ReflectionError("missing_history", f"Installed Claude session not found: {fork.path}")
        conversation = _ClaudeConversation(self, workspace, tool, fork, edit)
        try:
            yield conversation
        finally:
            if fork is not None and conversation.id is not None:
                # Claude records the fork beside the copy, in the store of the folder the session ran in, where it
                # would count as a session of that project.
                transcript = Path(fork.path).with_name(conversation.id)
                transcript.with_suffix(".jsonl").unlink(missing_ok=True)
                shutil.rmtree(transcript, ignore_errors=True)  # Its tool results and subagents, if any.


class _ClaudeConversation(Conversation):
    def __init__(
        self, runtime: ClaudeRuntime, workspace: Path, tool: ToolEndpoint, fork: InstalledSession | None, edit: bool
    ):
        self.runtime, self.workspace, self.tool, self.fork, self.edit = runtime, workspace, tool, fork, edit
        # Claude finds a session by the directory it was recorded in, so a fork runs there.
        self.cwd = Path(_claude_cwd(fork)) if fork is not None else workspace

    def send(self, message: str) -> str:
        servers = {"mcpServers": {SERVER: {"type": "http", "url": self.tool.url}}}
        command = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--tools",
            "Read,Grep,Glob,Edit,Write" if self.edit else "Read,Grep,Glob",
            "--allowedTools",
            ",".join([*self.tool.qualified, *(["Edit", "Write"] if self.edit else [])]),
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(servers),
            "--disable-slash-commands",
            "--settings",
            '{"disableAllHooks":true}',
            "--add-dir",
            str(self.workspace),
        ]
        command += ["--model", self.runtime.model] if self.runtime.model else []
        command += ["--effort", self.runtime.effort] if self.runtime.effort else []
        if self.id is not None:
            command += ["--resume", self.id]
        elif self.fork is not None:
            command += ["--resume", self.fork.session_id, "--fork-session"]
        if not self.cwd.is_dir():
            raise ReflectionError("missing_workspace", f"The session's working directory does not exist: {self.cwd}")
        output = _run(command, cwd=self.cwd, timeout=self.runtime.timeout, stdin=message)
        try:
            result = json.loads(output)
        except ValueError as exc:
            raise ReflectionError("invalid_output", "Claude did not print a JSON result") from exc
        usage, cost = result.get("usage") or {}, result.get("total_cost_usd")
        self.usage += Usage(input=usage.get("input_tokens", 0), output=usage.get("output_tokens", 0),
                            cache_read=usage.get("cache_read_input_tokens", 0),
                            cache_write=usage.get("cache_creation_input_tokens", 0),
                            reasoning=(usage.get("output_tokens_details") or {}).get("thinking_tokens", 0),
                            cost=float(cost) if isinstance(cost, (int, float)) else None)
        if result.get("is_error") or result.get("subtype") != "success":
            raise ReflectionError("incomplete", f"Claude turn failed: {str(result.get('result'))[:300]}")
        self.id = result.get("session_id") or self.id
        return str(result.get("result") or "")


def _claude_cwd(installed: InstalledSession) -> str:
    """The working directory recorded in an installed Claude transcript."""
    with Path(installed.path).open(encoding="utf-8") as stream:
        for line in stream:
            cwd = json.loads(line).get("cwd")
            if cwd:
                return str(cwd)
    raise ReflectionError("missing_workspace", "The installed Claude session records no working directory")


# --- Pi ----------------------------------------------------------------------------------------------------------


class PiRuntime(Runtime):
    harness = "pi"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        with tempfile.TemporaryDirectory(prefix="honed-pi-") as directory:
            extension = Path(directory) / "submit.ts"  # Registers the submit tool's server for this run only.
            config = json.dumps({"url": tool.url, "exposure": "direct"})
            extension.write_text(
                f"export default function (pi: any) {{ pi.registerMcpServer({json.dumps(SERVER)}, {config}); }}\n",
                encoding="utf-8",
            )
            yield _PiConversation(self, workspace, tool, extension, fork, Path(directory), edit)


class _PiConversation(Conversation):
    def __init__(
        self,
        runtime: PiRuntime,
        workspace: Path,
        tool: ToolEndpoint,
        extension: Path,
        fork: InstalledSession | None,
        prompts: Path,
        edit: bool = False,
    ):
        self.runtime, self.workspace, self.tool, self.extension = runtime, workspace, tool, extension
        self.fork, self.prompts, self.turns, self.edit = fork, prompts, 0, edit

    def send(self, message: str) -> str:
        self.turns += 1
        prompt = self.prompts / f"turn-{self.turns}.md"  # Long prompts go in a file Pi includes with @path.
        prompt.write_text(message, encoding="utf-8")
        command = [
            "pi",
            "-p",
            "--mode",
            "json",
            "--tools",
            ",".join(["read", "grep", "find", "ls", *(["edit", "write"] if self.edit else []), *self.tool.qualified]),
            "--no-extensions",
            "-e",
            "builtin:mcp",
            "-e",
            str(self.extension),
            "--no-skills",
            "--no-context-files",
            "--no-prompt-templates",
            "--no-themes",
        ]
        command += ["--model", self.runtime.model] if self.runtime.model else []
        command += ["--thinking", self.runtime.effort] if self.runtime.effort else []
        if self.id is not None:
            command += ["--session", self.id]
        elif self.fork is not None:
            command += ["--fork", str(self.fork.path)]
        else:
            command += ["--session-id", str(uuid.uuid4())]
        output = _run([*command, f"@{prompt}"], cwd=self.workspace, timeout=self.runtime.timeout)
        answer = None
        for line in output.split("\n"):  # JSON Lines: a string may hold U+2028, which splitlines() cuts at.
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Pi prints notices (e.g. a newly created session) as plain text.
            if event.get("type") == "session" and self.id is None:
                self.id = event.get("id")
            message_ = event.get("message") if event.get("type") == "message_end" else None
            if isinstance(message_, dict) and message_.get("role") == "assistant":
                answer = message_
                self.usage += _pi_usage(message_.get("usage"))  # One per model call in the turn.
        if answer is None:
            raise ReflectionError("incomplete", "Pi produced no assistant message")
        if answer.get("stopReason") in {"error", "aborted"}:
            raise ReflectionError("incomplete", f"Pi turn ended with {answer.get('stopReason')}")
        return "\n".join(
            block.get("text", "")
            for block in answer.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )


def _pi_usage(usage: Any) -> Usage:
    if not isinstance(usage, dict):
        return Usage()
    cost = usage.get("cost", {}).get("total") if isinstance(usage.get("cost"), dict) else None
    return Usage(input=usage.get("input", 0), output=usage.get("output", 0), cache_read=usage.get("cacheRead", 0),
                 cache_write=usage.get("cacheWrite", 0), cost=float(cost) if cost else None)


# --- OpenCode ----------------------------------------------------------------------------------------------------


class OpenCodeRuntime(Runtime):
    """Runs OpenCode's own CLI with a per-run MCP connection and restricted permissions."""

    harness = "opencode"

    @contextmanager
    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> Iterator[Conversation]:
        with tempfile.TemporaryDirectory(prefix="honed-opencode-") as directory:
            config = Path(directory) / "opencode.json"
            permissions = [
                {"action": "*", "resource": "*", "effect": "deny"},
                *[{"action": action, "resource": "*", "effect": "allow"}
                  for action in ("read", "glob", "grep", "list")],
                {"action": "external_directory", "resource": "*", "effect": "deny"},
                {"action": "shell", "resource": "*", "effect": "deny"},
                {"action": "subagent", "resource": "*", "effect": "deny"},
                {"action": "edit", "resource": "*", "effect": "allow" if edit else "deny"},
                *[{"action": f"honed_{name}", "resource": "*", "effect": "allow"} for name in tool.tools],
            ]
            config.write_text(json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "mcp": {"servers": {"honed": {"type": "remote", "url": tool.url, "codemode": False}}},
                "permissions": permissions,
            }), encoding="utf-8")
            environment = {"OPENCODE_CONFIG": str(config), "OPENCODE_DISABLE_AUTOUPDATE": "true"}
            conversation = _OpenCodeConversation(self, workspace, tool, environment)
            imported_id = None
            if fork is not None:
                if not fork.path.is_file():
                    raise ReflectionError("missing_history", f"Installed OpenCode session not found: {fork.path}")
                _run(["opencode", "session", "import", str(fork.path), "--directory", str(workspace),
                      "--standalone"], cwd=workspace,
                     timeout=self.timeout, env_overrides=environment)
                imported_id = fork.session_id
            conversation.fork_id = imported_id
            try:
                yield conversation
            finally:
                for session_id in (conversation.id, imported_id):
                    if session_id:
                        try:
                            _run(["opencode", "session", "delete", session_id, "--standalone"],
                                 cwd=workspace, timeout=min(self.timeout, 30), env_overrides=environment)
                        except ReflectionError:
                            pass


class _OpenCodeConversation(Conversation):
    def __init__(self, runtime: OpenCodeRuntime, workspace: Path, tool: ToolEndpoint,
                 environment: dict[str, str]):
        self.runtime, self.workspace, self.tool, self.environment = runtime, workspace, tool, environment
        self.id: str | None = None
        self.fork_id: str | None = None

    def send(self, message: str) -> str:
        command = ["opencode", "run", "--format", "json", "--standalone", "--auto"]
        if self.runtime.model:
            model = f"{self.runtime.model}#{self.runtime.effort}" if self.runtime.effort else self.runtime.model
            command.extend(["--model", model])
        if self.fork_id and not self.id:
            command.extend(["--session", self.fork_id, "--fork"])
        elif self.id:
            command.extend(["--session", self.id])
        command.append(message)
        output = _run(command, cwd=self.workspace, timeout=self.runtime.timeout,
                      env_overrides=self.environment)
        texts: list[str] = []
        for line in output.split("\n"):  # JSON Lines: a string may hold U+2028, which splitlines() cuts at.
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self.id = event.get("sessionID") or self.id
            part = event.get("part") or {}
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            if event.get("type") == "step_finish":
                self.usage += _opencode_usage(part.get("tokens"), part.get("cost"))
        if not texts:
            raise ReflectionError("incomplete", "OpenCode produced no assistant text")
        return "\n".join(texts)


def _opencode_usage(tokens: Any, cost: Any) -> Usage:
    if not isinstance(tokens, dict):
        tokens = {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return Usage(input=tokens.get("input", 0), output=tokens.get("output", 0),
                 cache_read=cache.get("read", 0), cache_write=cache.get("write", 0),
                 reasoning=tokens.get("reasoning", 0), cost=float(cost) if isinstance(cost, (int, float)) else None)


# --- Antigravity -------------------------------------------------------------------------------------------------


class AntigravityRuntime(Runtime):
    """Not available: Antigravity reads MCP servers only from the user's own ``mcp_config.json``."""

    harness = "antigravity"
    can_fork = False

    def conversation(
        self, workspace: Path, tool: ToolEndpoint, *, fork: InstalledSession | None = None, edit: bool = False
    ) -> AbstractContextManager[Conversation]:
        raise ReflectionError("capability", "Antigravity cannot connect the submit_reflection tool for one run")


RUNTIMES: dict[str, type[Runtime]] = {
    "codex": CodexRuntime,
    "claude": ClaudeRuntime,
    "pi": PiRuntime,
    "opencode": OpenCodeRuntime,
    "antigravity": AntigravityRuntime,
}
