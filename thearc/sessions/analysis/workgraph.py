"""A compact, deterministic graph view of one agent trajectory.

The graph is a projection over immutable session events. It keeps source event
IDs for bounded expansion; it does not infer the agent's intent or causes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from ..models import Event
from ..scanner.patterns import FrustrationSpike, StubbornToolLoop
from ..session import EventSequence, Trajectory
from ..trajectories import _stream


@dataclass(frozen=True)
class WorkNode:
    """A compact step or a deterministic behavioral signal."""

    id: str
    kind: str
    summary: str
    event_ids: tuple[str, ...] = ()
    timestamp: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class WorkEdge:
    """A directly observed structural relation between graph nodes."""

    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class OverviewItem:
    """A short, source-linked excerpt used to orient a graph reader."""

    event_id: str
    text: str
    timestamp: str | None = None


@dataclass(frozen=True)
class ConversationOverview:
    """Recorded opening prompt and first/final assistant text messages."""

    first_user_prompt: OverviewItem | None = None
    first_agent_response: OverviewItem | None = None
    last_agent_response: OverviewItem | None = None

    def to_dict(self) -> dict[str, dict[str, str | None] | None]:
        return {
            name: (
                {"event_id": item.event_id, "text": item.text, "timestamp": item.timestamp}
                if item is not None else None
            )
            for name, item in (
                ("first_user_prompt", self.first_user_prompt),
                ("first_agent_response", self.first_agent_response),
                ("last_agent_response", self.last_agent_response),
            )
        }


def _compact(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"… [{len(text) - limit} chars omitted]"


def _mermaid_label(value: str) -> str:
    """Encode text as Mermaid-safe label content while keeping it readable."""
    return "".join(
        "<br/>" if char == "\n" else char if char.isalnum() or char in " _-." else f"#{ord(char)};"
        for char in value
    )


def render_mermaid_png(diagram: str, filename: str | Path, *, timeout: float = 60) -> Path:
    """Render Mermaid source locally to PNG using Mermaid CLI (`mmdc`)."""
    output = Path(filename)
    if output.suffix.lower() != ".png":
        raise ValueError("PNG output path must end in .png")
    executable = shutil.which("mmdc")
    if executable is None:
        raise RuntimeError(
            "PNG export requires Mermaid CLI (`mmdc`) on PATH; install it with "
            "`npm install --global @mermaid-js/mermaid-cli`. Mermaid text export needs no extra dependency."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="thearc-workgraph-") as temp_dir:
        source = Path(temp_dir) / "diagram.mmd"
        source.write_text(diagram, encoding="utf-8")
        try:
            subprocess.run(
                [executable, "-i", str(source), "-o", str(output)],
                capture_output=True,
                check=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"Mermaid PNG rendering failed: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Mermaid PNG rendering exceeded {timeout:g} seconds") from exc
    return output


def _event_summary(event: Event, *, excerpt_chars: int) -> str:
    """Summarize only recorded fields; long tool output is left for expansion."""
    if event.kind == "message":
        label = "user" if event.role == "user" else "assistant"
        return f"{label} message: {_compact(event.text, excerpt_chars)}"
    if event.kind == "tool_call":
        action = event.tool_name or event.action_kind or "tool"
        args = _compact(event.arguments, excerpt_chars)
        return f"call {action}" + (f": {args}" if args and args != "null" else "")
    if event.kind == "tool_result":
        action = event.tool_name or event.action_kind or "tool"
        status = event.status or "status unreported"
        excerpt = _compact(event.text, excerpt_chars)
        if event.status in {"failure", "error"}:
            return f"{action} returned {status}: {excerpt}"
        return f"{action} returned {status}; {len(event.text)} output chars"
    return f"{event.kind}: {_compact(event.text or event.native_type or '', excerpt_chars)}"


class WorkGraph:
    """A selected event sequence's compact graph with redacted source expansion.

    ``source`` may be a Session, Thread, or Trajectory. Multi-stream sources are
    rendered as separate components; ordering and deterministic signals stay
    within each transcript stream. Source details are redacted before retention.
    """

    def __init__(
        self,
        source: EventSequence | None = None,
        *,
        nodes: Sequence[WorkNode] | None = None,
        edges: Sequence[WorkEdge] = (),
        source_events: Mapping[str, dict[str, Any]] | None = None,
        source_event_count: int = 0,
        omitted_event_count: int = 0,
        overview: ConversationOverview | None = None,
        trajectory_id: str | None = None,
        session_id: str | None = None,
        boundary: str | None = None,
        redacted: Mapping[str, int] | None = None,
        event_stream_count: int = 1,
    ) -> None:
        if source is not None:
            if nodes is not None or edges or source_events:
                raise ValueError("Pass a thread or projected graph fields, not both")
            graph = type(self).from_events(
                source.selected_events,
                trajectory_id=source.id if isinstance(source, Trajectory) else None,
                session_id=getattr(source, "session_id", None),
                boundary=source.boundary if isinstance(source, Trajectory) else None,
            )
            self.__dict__.update(graph.__dict__)
            return
        self.nodes = tuple(nodes or ())
        self.edges = tuple(edges)
        self._source_events = dict(source_events or {})
        self._expandable_ids = {event_id for node in self.nodes if node.kind != "signal"
                                for event_id in node.event_ids}
        self.source_event_count = source_event_count
        self.omitted_event_count = omitted_event_count
        self.overview = overview or ConversationOverview()
        self.trajectory_id = trajectory_id
        self.session_id = session_id
        self.boundary = boundary
        self.redacted = dict(redacted or {})
        self.event_stream_count = event_stream_count

    @classmethod
    def from_events(
        cls,
        events: Sequence[Event | Mapping[str, Any]],
        *,
        trajectory_id: str | None = None,
        session_id: str | None = None,
        boundary: str | None = None,
        excerpt_chars: int = 220,
        redactor: Callable[[dict[str, Any], Counter[str]], dict[str, Any]] | None = None,
    ) -> WorkGraph:
        """Build a stable graph without model calls or semantic classification."""
        if excerpt_chars < 32:
            raise ValueError("excerpt_chars must be at least 32")
        counts: Counter[str] = Counter()
        if redactor is None:
            from thearc.sessions.redact import Redaction

            # The graph does not fork a harness session, so its cwd need not be
            # preserved for the runtime and should be redacted like other paths.
            policy = Redaction(keep=frozenset())
            redact = lambda value, tally: policy.value(value, tally)
        else:
            redact = redactor
        parsed: list[Event] = []
        source: dict[str, dict[str, Any]] = {}
        for item in events:
            event = Event.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
            safe = redact(event.model_dump(mode="json"), counts)
            safe_event = Event.model_validate(safe)
            if safe_event.id in source:
                raise ValueError(f"duplicate source event ID: {safe_event.id}")
            parsed.append(safe_event)
            source[safe_event.id] = safe

        # Claude logs may contain an api-request-blob alongside the native
        # assistant/user record for the same interaction. Keep the primary
        # record when the normalized payload is identical, while retaining
        # unmatched blobs as potentially unique evidence. System records are
        # harness context rather than trajectory actions and are not projected.
        def content_key(event: Event) -> str:
            payload = {
                "kind": event.kind,
                "role": event.role,
                "tool_name": event.tool_name,
                "action_kind": event.action_kind,
                "call_id": event.call_id,
                "status": event.status,
                "text": event.text,
                "arguments": event.arguments,
            }
            return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

        primary_keys = {
            content_key(event) for event in parsed
            if event.native_type != "api-request-blob" and event.role != "system"
        }
        visible = [
            event for event in parsed
            if event.role != "system"
            and not (event.native_type == "api-request-blob" and content_key(event) in primary_keys)
        ]
        visible_ids = {event.id for event in visible}
        source = {event_id: source[event_id] for event_id in visible_ids}

        def overview_item(event: Event | None) -> OverviewItem | None:
            if event is None:
                return None
            return OverviewItem(event.id, _compact(event.text, 480), event.timestamp)

        streams: dict[tuple, list[Event]] = defaultdict(list)
        for event in visible:
            streams[_stream(event)].append(event)
        if len(streams) == 1:
            only_stream = next(iter(streams.values()))
            user_prompt = next(
                (event for event in only_stream if event.kind == "message" and event.role == "user"
                 and event.text.strip()),
                None,
            )
            agent_messages = [
                event for event in only_stream
                if event.kind == "message" and event.role == "assistant" and event.text.strip()
            ]
            overview = ConversationOverview(
                first_user_prompt=overview_item(user_prompt),
                first_agent_response=overview_item(agent_messages[0] if agent_messages else None),
                last_agent_response=overview_item(agent_messages[-1] if agent_messages else None),
            )
        else:
            # A session can contain parallel threads or transcript segments with no
            # single total order. Don't present arbitrary file order as conversation order.
            overview = ConversationOverview()

        nodes: list[WorkNode] = []
        edges: list[WorkEdge] = []
        event_node: dict[str, str] = {}
        event_by_id = {event.id: event for event in parsed}
        call_nodes: dict[tuple[str, str, str, str], str] = {}
        result_nodes: dict[tuple[str, str, str, str], str] = {}
        native_nodes: dict[tuple[str, str, str, str], str] = {}
        parent_links: list[tuple[str, str]] = []

        for event in visible:
            # Lifecycle/index metadata remains expandable through event references
            # attached to visible steps, but is not repeated as a graph node.
            if event.kind == "metadata":
                continue
            node_id = f"event:{event.id}"
            node = WorkNode(
                id=node_id,
                kind=event.kind,
                summary=_event_summary(event, excerpt_chars=excerpt_chars),
                event_ids=(event.id,),
                timestamp=event.timestamp,
                status=event.status,
            )
            if nodes:
                previous = event_by_id[nodes[-1].event_ids[0]]
                if _stream(previous) == _stream(event):
                    edges.append(WorkEdge(nodes[-1].id, node_id, "next"))
            nodes.append(node)
            event_node[event.id] = node_id
            stream = (event.source_id, event.session_id, event.run_id)
            if event.kind == "tool_call" and event.call_id:
                call_nodes[(*stream, event.call_id)] = node_id
            elif event.kind == "tool_result" and event.call_id:
                result_nodes[(*stream, event.call_id)] = node_id
            if event.native_id:
                native_nodes[(*stream, event.native_id)] = node_id
            if event.parent_native_id:
                parent_links.append((event.id, event.parent_native_id))

        for child_id, parent_native_id in parent_links:
            child = event_by_id[child_id]
            parent = native_nodes.get((child.source_id, child.session_id, child.run_id, parent_native_id))
            if parent and child_id in event_node:
                edges.append(WorkEdge(parent, event_node[child_id], "parent"))

        for key, call_node in call_nodes.items():
            result_node = result_nodes.get(key)
            if result_node:
                edges.append(WorkEdge(call_node, result_node, "returned"))

        matches = []
        for stream_events in streams.values():
            for pattern in (StubbornToolLoop(), FrustrationSpike()):
                matches.extend(pattern.find_matches(stream_events))
        for match in matches:
            involved = tuple(event.id for event in match.events if event.id in event_node)
            if not involved:
                continue
            signal_id = f"signal:{match.pattern_name}:{involved[0]}"
            nodes.append(WorkNode(signal_id, "signal", match.description, involved))
            edges.extend(WorkEdge(event_node[event_id], signal_id, "evidence_for") for event_id in involved)

        return cls(nodes=nodes, edges=edges, source_events=source, source_event_count=len(parsed),
                   omitted_event_count=len(parsed) - len(visible),
                   overview=overview,
                   trajectory_id=trajectory_id, session_id=session_id, boundary=boundary, redacted=counts,
                   event_stream_count=len(streams))

    @classmethod
    def from_trajectory(cls, trajectory: Trajectory, **options: Any) -> WorkGraph:
        """Build from a ``Trajectory`` without requiring callers to unpack its metadata."""
        return cls.from_events(
            trajectory.selected_events,
            trajectory_id=getattr(trajectory, "id", None),
            session_id=getattr(trajectory, "session_id", None),
            boundary=getattr(trajectory, "boundary", None),
            **options,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_id": self.trajectory_id,
            "session_id": self.session_id,
            "boundary": self.boundary,
            "source_event_count": self.source_event_count,
            "omitted_event_count": self.omitted_event_count,
            "event_stream_count": self.event_stream_count,
            "overview": self.overview.to_dict(),
            "visible_event_count": sum(node.kind != "signal" for node in self.nodes),
            "nodes": [node.__dict__ for node in self.nodes],
            "edges": [edge.__dict__ for edge in self.edges],
        }

    def to_markdown(self) -> str:
        """Render a concise prompt view while retaining IDs for source expansion."""
        header = [
            "## WorkGraph (deterministic event projection)",
            (f"Trajectory `{self.trajectory_id or 'unknown'}`; session `{self.session_id or 'unknown'}`; "
             f"boundary `{self.boundary or 'unknown'}`; {self.source_event_count} source events, "
             f"{self.omitted_event_count} omitted as system context or duplicate records; "
             f"{self.event_stream_count} event stream(s)."),
            ("Nodes report recorded events or deterministic pattern matches in recorded order within each run. "
             "Edges show native parentage, tool-call results, or which events support a pattern; they do not "
             "assert causes."),
            "",
        ]
        if self.event_stream_count > 1:
            header.append("No single conversation overview is shown because this input contains multiple streams.")
        else:
            header.append("### Conversation overview")
            for label, item in (
                ("First user prompt", self.overview.first_user_prompt),
                ("First agent text response", self.overview.first_agent_response),
                ("Last agent text response", self.overview.last_agent_response),
            ):
                if item is None:
                    header.append(f"- {label}: no recorded message")
                else:
                    header.append(
                        f"- {label} (`event:{item.event_id}`, {item.timestamp or 'time unknown'}): {item.text}"
                    )
        header.append("")
        header.extend(f"- `{node.id}` [{node.timestamp or 'time unknown'}] {node.summary}"
                      for node in self.nodes)
        structural = [edge for edge in self.edges if edge.relation != "next"]
        if structural:
            header.extend(["", "Edges:", *(f"- `{edge.source}` --{edge.relation}--> `{edge.target}`"
                                          for edge in structural)])
        signal_count = sum(node.kind == "signal" for node in self.nodes)
        header.append(f"\nDeterministic signals: {signal_count}. Source-event expansion is available with "
                      "`expand_source_events(event_ids, max_chars)`; request only IDs needed to verify a conclusion.")
        return "\n".join(header)

    def to_mermaid(
        self, *, direction: str = "LR", include_metadata: bool = True, include_overview: bool = True
    ) -> str:
        """Render the graph as a Mermaid flowchart with compact node annotations."""
        if direction not in {"LR", "RL", "TB", "BT"}:
            raise ValueError("direction must be LR, RL, TB, or BT")
        node_ids = {node.id: f"n{index}" for index, node in enumerate(self.nodes)}
        lines = [f"flowchart {direction}"]
        overview_items = (
            ("overview_prompt", "First user prompt", self.overview.first_user_prompt),
            ("overview_first_response", "First agent response", self.overview.first_agent_response),
            ("overview_last_response", "Last agent response", self.overview.last_agent_response),
        )
        if include_overview and any(item for _, _, item in overview_items):
            lines.append('    subgraph conversation_overview["Conversation overview"]')
            ordered_overview_nodes: list[tuple[str, str]] = []
            for node_id, label, item in overview_items:
                if item is None:
                    continue
                detail = _compact(item.text, 240)
                content = f"{label}: {detail} (event:{item.event_id})"
                lines.append(f'        {node_id}["{_mermaid_label(content)}"]')
                if not ordered_overview_nodes or item.event_id != ordered_overview_nodes[-1][1]:
                    ordered_overview_nodes.append((node_id, item.event_id))
            for (earlier, _), (later, _) in pairwise(ordered_overview_nodes):
                lines.append(f"        {earlier} -.-> {later}")
            lines.append("    end")
        for node in self.nodes:
            label = node.summary
            if include_metadata:
                metadata = [f"kind: {node.kind}"]
                if node.timestamp:
                    metadata.append(f"time: {node.timestamp}")
                if node.status:
                    metadata.append(f"status: {node.status}")
                if node.event_ids:
                    metadata.append(f"events: {', '.join(node.event_ids)}")
                label += "\n" + " · ".join(metadata)
            lines.append(f'    {node_ids[node.id]}["{_mermaid_label(label)}"]')
        for edge in self.edges:
            label = "" if edge.relation == "next" else f'|"{_mermaid_label(edge.relation)}"|'
            lines.append(f"    {node_ids[edge.source]} -->{label} {node_ids[edge.target]}")
        classes = {
            "message": "fill:#e8f1ff,stroke:#4776b4,color:#172b45",
            "thinking": "fill:#f1f1f1,stroke:#888,color:#333",
            "tool_call": "fill:#e8f7ef,stroke:#43845b,color:#193b26",
            "tool_result": "fill:#fff7e6,stroke:#b38a36,color:#443719",
            "signal": "fill:#fff0cc,stroke:#b7791f,color:#382b12",
        }
        for kind, style in classes.items():
            lines.append(f"    classDef {kind} {style}")
        lines.append("    classDef user_message fill:#efe8ff,stroke:#7554a6,color:#302044")
        lines.append("    classDef overview fill:#f1edfa,stroke:#78679b,color:#302b3a")
        lines.extend(
            f"    class {node_id} overview"
            for node_id, _, item in overview_items
            if include_overview and item is not None
        )
        for node in self.nodes:
            style_class = node.kind if node.kind in classes else ""
            if node.kind == "message" and node.summary.startswith("user message:"):
                style_class = "user_message"
            if style_class:
                lines.append(f"    class {node_ids[node.id]} {style_class}")
        return "\n".join(lines) + "\n"

    def render_png(
        self,
        filename: str | Path,
        *,
        direction: str = "LR",
        include_metadata: bool = True,
        timeout: float = 60,
    ) -> Path:
        """Render Mermaid locally to PNG using `mmdc` (Mermaid CLI)."""
        return render_mermaid_png(
            self.to_mermaid(direction=direction, include_metadata=include_metadata), filename, timeout=timeout
        )

    def expand_source_events(self, event_ids: Sequence[str], *, max_chars: int = 6000) -> str:
        """Return bounded, redacted canonical event details for visible graph nodes."""
        if not 1 <= len(event_ids) <= 8:
            raise ValueError("request between 1 and 8 event IDs")
        if not 256 <= max_chars <= 12000:
            raise ValueError("max_chars must be between 256 and 12000")
        unknown = [event_id for event_id in event_ids if event_id not in self._expandable_ids]
        if unknown:
            raise ValueError(f"event ID is outside this trajectory: {unknown[0]}")
        per_event = max(128, (max_chars - 16 * len(event_ids)) // len(event_ids))
        selected = []
        for event_id in event_ids:
            event = self._source_events[event_id]
            data = {key: event.get(key) for key in (
                "id", "timestamp", "kind", "role", "tool_name", "action_kind", "call_id", "status",
                "native_type", "native_id", "parent_native_id", "run_id",
            ) if event.get(key) is not None}
            reference = event.get("reference") or {}
            if reference:
                data["reference"] = {key: reference[key] for key in ("path", "line", "json_pointer")
                                      if key in reference}
            data["text"] = _compact(event.get("text", ""), max(32, per_event // 3))
            if event.get("arguments") is not None:
                data["arguments"] = _compact(event["arguments"], max(32, per_event // 3))
            while len(json.dumps(data, ensure_ascii=False, sort_keys=True)) > per_event:
                fields = [key for key in ("text", "arguments")
                          if isinstance(data.get(key), str) and len(data[key]) > 32]
                if not fields:
                    if "reference" in data:
                        data.pop("reference")
                        continue
                    if "arguments" in data:
                        data.pop("arguments")
                        continue
                    if "text" in data:
                        data.pop("text")
                        continue
                    break
                key = max(fields, key=lambda name: len(data[name]))
                data[key] = _compact(data[key], max(16, len(data[key]) // 2))
            selected.append(data)
        result = json.dumps(selected, ensure_ascii=False)
        if len(result) > max_chars:
            raise ValueError("expanded events exceed max_chars; request fewer events or a larger bound")
        return result

    def expansion_tool(self):
        """An MCP tool spec for bounded event lookup; imports MCP only when requested."""
        from thearc.reflection.mcp import ToolSpec

        def expand_source_events(event_ids: list[str], max_chars: int = 6000) -> str:
            """Expand 1–8 visible source event IDs, within the supplied character limit."""
            return self.expand_source_events(event_ids, max_chars=max_chars)

        return ToolSpec(
            expand_source_events,
            "Read redacted source events for visible WorkGraph node IDs from this trajectory only. "
            "Request only the events needed to verify or disambiguate a finding; output is bounded.",
            read_only=True,
        )
