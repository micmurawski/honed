"""Step 0: a canonical event table, one case per thread branch.

A case is one independently ordered stream: a session's thread (its main agent, or a subagent), and when the
harness records a tree (``parent_native_id`` with several children, as Pi does) each root-to-leaf path of it. The
branch that holds the stream's last event is the main one; the others are marked abandoned, a signal of their own.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ...models import Event
from ...redact import Redaction

OUTPUT_LIMIT = 500  # Characters of a tool's output kept, plus its error lines.
_ERROR_LINE = re.compile(
    r"(traceback \(most recent call last\)|\w*(error|exception)\b|\bfailed\b|\bfatal\b|not found|denied|"
    r"exit code [1-9]|assert)",
    re.IGNORECASE,
)
# A result without a recorded status counts as failed only when its output opens like an error.
_FAILED_START = re.compile(
    r"^\s*(exit code [1-9]|error\b|traceback \(most recent call last\)|fatal:|<tool_use_error>|"
    r"command not found|permission denied|no such file or directory)",
    re.IGNORECASE,
)
_API_ERROR = re.compile(r"^\s*(api error|request failed|rate limit|overloaded|internal server error)", re.IGNORECASE)
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
_PATH_KEYS = ("file_path", "path", "filePath", "notebook_path", "target_file", "AbsolutePath", "TargetFile")


@dataclass
class CaseEvent:
    """One row of the canonical event table."""

    case_id: str
    session_id: str
    thread_id: str
    branch_id: str
    seq: int
    event_id: str
    timestamp: str | None
    harness: str
    role: str | None
    kind: str  # user_msg, assistant_text, thinking, tool_call, tool_result, compaction, model_change, usage, api_error
    tool_name: str | None = None
    action_kind: str | None = None
    call_id: str | None = None
    args_summary: str = ""
    command: str | None = None
    paths: tuple[str, ...] = ()
    result_status: str | None = None  # ok, fail
    error_text: str = ""
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    cwd: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {name: (list(value) if isinstance(value, tuple) else value) for name, value in self.__dict__.items()}


@dataclass
class Case:
    """One linear stream of events: a thread, or one branch of a tree-shaped thread."""

    case_id: str
    session_id: str
    native_session_id: str
    thread_id: str
    branch_id: str
    harness: str
    root: bool
    abandoned_branch: bool
    title: str | None
    cwd: str | None
    started_at: str | None
    events: list[CaseEvent] = field(default_factory=list)
    parent_thread_id: str | None = None  # The thread that spawned this one (a subagent's parent).

    @property
    def model(self) -> str | None:
        """The model that answered most of this case, when the record names one."""
        counts: dict[str, int] = {}
        for event in self.events:
            if event.model:
                counts[event.model] = counts.get(event.model, 0) + 1
        return max(counts, key=lambda name: counts[name]) if counts else None

    @property
    def month(self) -> str:
        return (self.started_at or "unknown")[:7]

    @property
    def tokens(self) -> int:
        return sum(event.tokens_in + event.tokens_out for event in self.events)

    @property
    def cost(self) -> float:
        return sum(event.cost for event in self.events)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value is not None else ""


def truncate_output(text: str) -> tuple[str, str]:
    """The first ``OUTPUT_LIMIT`` characters and the error lines (up to five) of a tool's output."""
    errors = [line.strip()[:200] for line in text.splitlines() if _ERROR_LINE.search(line)][:5]
    head = text[:OUTPUT_LIMIT]
    return head, "\n".join(errors)


def _arguments(event: Event) -> Any:
    arguments = event.arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return arguments
    return arguments


def _paths(event: Event, arguments: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(arguments, dict):
        for key in _PATH_KEYS:
            if isinstance(arguments.get(key), str):
                found.append(arguments[key])
        for key in ("input", "patch", "content"):
            if isinstance(arguments.get(key), str) and "*** " in arguments[key]:
                found += _PATCH_FILE.findall(arguments[key])
    elif isinstance(arguments, str) and "*** " in arguments:
        found += _PATCH_FILE.findall(arguments)
    return tuple(dict.fromkeys(path.strip() for path in found if path.strip()))


def _command(event: Event) -> str | None:
    from ...scanner.shell import _commands

    commands = [command for command, *_ in _commands(event)]
    return "\n".join(commands) if commands else None


def _summary(event: Event, arguments: Any, command: str | None, paths: tuple[str, ...]) -> str:
    if command:
        return command[:200]
    if paths:
        return ", ".join(paths)[:200]
    if isinstance(arguments, dict):
        for key in ("pattern", "query", "url", "description", "prompt", "subagent_type"):
            if isinstance(arguments.get(key), str):
                return f"{key}={arguments[key]}"[:200]
    return _text(arguments)[:200]


def _usage(raw: dict[str, Any]) -> tuple[int, int, float, str | None, str | None]:
    """Tokens in and out, cost, model, and a key identifying the model reply they belong to (counted once)."""
    message = _dict(raw.get("message"))
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
    model = message.get("model") if isinstance(message.get("model"), str) else None
    if usage is not None:
        if "input_tokens" in usage:  # Claude
            tokens_in = sum(int(usage.get(key) or 0) for key in
                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))  # fmt: skip
            return tokens_in, int(usage.get("output_tokens") or 0), 0.0, model, message.get("id") or raw.get("uuid")
        if "input" in usage:  # Pi
            cost = usage.get("cost", {})
            total = float(cost.get("total") or 0) if isinstance(cost, dict) else 0.0
            tokens_in = sum(int(usage.get(key) or 0) for key in ("input", "cacheRead", "cacheWrite"))
            return tokens_in, int(usage.get("output") or 0), total, model, raw.get("id")
    payload = _dict(raw.get("payload"))
    if payload.get("type") == "token_count" and isinstance(payload.get("info"), dict):  # Codex
        last = payload["info"].get("last_token_usage") or {}
        return int(last.get("input_tokens") or 0), int(last.get("output_tokens") or 0), 0.0, None, None
    info = _dict(raw.get("info"))
    if isinstance(info.get("tokens"), dict):  # OpenCode
        tokens = info["tokens"]
        return int(tokens.get("input") or 0), int(tokens.get("output") or 0), float(info.get("cost") or 0), \
            info.get("modelID"), info.get("id")  # fmt: skip
    return 0, 0, 0.0, model, None


def _model_change(raw: dict[str, Any], native_type: str | None) -> str | None:
    payload = _dict(raw.get("payload"))
    if native_type == "turn_context" or raw.get("type") == "turn_context":
        return payload.get("model") if isinstance(payload.get("model"), str) else None
    if native_type == "model_change" or raw.get("type") == "model_change":
        for key in ("modelId", "model", "to"):
            if isinstance(raw.get(key), str):
                return raw[key]
        return "unknown"
    return None


def _compaction(raw: dict[str, Any], native_type: str | None) -> bool:
    kinds = {str(native_type or ""), str(raw.get("type") or ""), str(raw.get("subtype") or "")}
    payload = _dict(raw.get("payload"))
    kinds.add(str(payload.get("type") or ""))
    return any("compact" in kind.lower() for kind in kinds) or bool(raw.get("isCompactSummary"))


def _api_error(event: Event, raw: dict[str, Any]) -> bool:
    payload = _dict(raw.get("payload"))
    if raw.get("isApiErrorMessage") or payload.get("type") in {"error", "stream_error"}:
        return True
    return event.kind == "message" and event.role == "assistant" and bool(_API_ERROR.match(event.text or ""))


def canonical(event: Event, redaction: Redaction | None = None) -> CaseEvent | None:
    """One normalized event as a table row (without its case fields); None for records with nothing to analyse."""
    raw = event.raw if isinstance(event.raw, dict) else {}
    tokens_in, tokens_out, cost, model, _ = _usage(raw)
    row = CaseEvent("", event.session_id, event.run_id, "", 0, event.id, event.timestamp, event.harness,
                    event.role, "", cwd=event.cwd, tokens_in=tokens_in, tokens_out=tokens_out, cost=cost,
                    model=model)  # fmt: skip
    clean = redaction.text if redaction is not None else (lambda text: text)
    if _api_error(event, raw):
        row.kind, row.text = "api_error", clean((event.text or "")[:300])
    elif event.kind == "message" and event.role == "user":
        row.kind, row.text = "user_msg", clean(event.text or "")[:2000]
    elif event.kind == "message" and event.role == "assistant":
        row.kind, row.text = "assistant_text", clean(event.text or "")[:2000]
    elif event.kind == "message":
        return None  # System and developer prompts are configuration, not behaviour.
    elif event.kind == "thinking":
        row.kind = "thinking"
    elif event.kind == "tool_call":
        arguments = _arguments(event)
        row.kind, row.tool_name, row.action_kind, row.call_id = "tool_call", event.tool_name, event.action_kind, \
            event.call_id  # fmt: skip
        row.command = _command(event)
        row.paths = _paths(event, arguments)
        row.args_summary = clean(_summary(event, arguments, row.command, row.paths))
    elif event.kind == "tool_result":
        head, errors = truncate_output(event.text or "")
        failed = event.status == "failure" or (event.status is None and bool(_FAILED_START.match(head)))
        row.kind, row.tool_name, row.action_kind, row.call_id = "tool_result", event.tool_name, event.action_kind, \
            event.call_id  # fmt: skip
        row.result_status = "fail" if failed else "ok"
        row.text, row.error_text = clean(head), clean(errors)
    elif (changed := _model_change(raw, event.native_type)) is not None:
        row.kind, row.model = "model_change", changed
    elif _compaction(raw, event.native_type):
        row.kind = "compaction"
    elif tokens_in or tokens_out:
        row.kind = "usage"
    else:
        return None
    return row


def _dedupe_usage(rows: list[CaseEvent], events: list[Event]) -> None:
    """A model reply split into several events carries its usage once, on the first of them."""
    seen: set[str] = set()
    for row, event in zip(rows, events, strict=True):
        key = _usage(event.raw if isinstance(event.raw, dict) else {})[4]
        if key is None:
            continue
        if key in seen:
            row.tokens_in = row.tokens_out = 0
            row.cost = 0.0
        seen.add(key)


def _branches(events: list[Event]) -> list[tuple[str, list[Event], bool]]:
    """Root-to-leaf paths of a tree-shaped stream (``branch_id``, events, abandoned); one path when it is linear.

    Events sharing a native ID are one node; events without one stay with the node before them.
    """
    nodes: dict[str, list[Event]] = {}
    order: list[str] = []
    parents: dict[str, str | None] = {}
    current = None
    for event in events:
        node = event.native_id or current or "__start__"
        if node not in nodes:
            nodes[node], parents[node] = [], event.parent_native_id
            order.append(node)
        nodes[node].append(event)
        current = node
    children: dict[str, list[str]] = {}
    for node in order:
        parent = parents[node]
        if parent in nodes:
            children.setdefault(parent, []).append(node)
    if not any(len(kids) > 1 for kids in children.values()):
        return [("main", events, False)]
    roots = [node for node in order if parents[node] not in nodes]
    leaves = [node for node in order if node not in children]
    last = order[-1]
    paths = []
    for leaf in leaves:
        path: list[str] = []
        step: str | None = leaf
        while step is not None and step in nodes:
            path.append(step)
            step = parents[step] if parents[step] in nodes else None
        path.reverse()
        if path and path[0] in roots:
            stream = [event for step in path for event in nodes[step]]
            paths.append((leaf[:12], stream, leaf != last))
    return paths


def cases_from_threads(threads: Iterable[Any], *, redaction: Redaction | None = None) -> Iterator[Case]:
    """Cases from ``Thread`` objects (see ``thearc.sessions.threads``), one per branch."""
    for thread in threads:
        session = thread.session
        metadata = session.metadata
        events = [Event.model_validate(e.model_dump()) if not isinstance(e, Event) else e for e in thread.events]
        for branch_id, stream, abandoned in _branches(events):
            case_id = f"{thread.id[:12]}/{branch_id}" if branch_id != "main" else thread.id[:12]
            case = Case(case_id, session.id, metadata.native_id, thread.id, branch_id, metadata.harness,
                        thread.parent_id is None, abandoned, metadata.title, metadata.cwd,
                        metadata.started_at, parent_thread_id=thread.parent_id)  # fmt: skip
            rows: list[CaseEvent] = []
            kept: list[Event] = []
            for event in stream:
                row = canonical(event, redaction)
                if row is None:
                    continue
                row.case_id, row.branch_id, row.seq = case_id, branch_id, len(rows)
                rows.append(row)
                kept.append(event)
            _dedupe_usage(rows, kept)
            model = None
            for row in rows:  # A model change applies to what follows it.
                if row.kind == "model_change":
                    model = row.model
                elif row.model is None and model is not None and row.kind != "usage":
                    row.model = model
            case.events = rows
            yield case
