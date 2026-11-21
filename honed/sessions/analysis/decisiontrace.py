"""Experimental, evidence-linked decision trace derived from recorded sessions.

This is an observable action trace, not a reconstruction of hidden model reasoning.
Tool calls and their correlated results are presented as one action episode.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from ..models import Event
from ..session import EventSequence, Trajectory
from ..trajectories import _stream
from .workgraph import WorkGraph, _compact, _mermaid_label, render_mermaid_png


@dataclass(frozen=True)
class DecisionNode:
    """An observed request, response, action episode, or behavioral signal."""

    id: str
    kind: str
    summary: str
    event_ids: tuple[str, ...]
    timestamp: str | None = None
    completed_at: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class DecisionEdge:
    """A recorded sequence, parent, or signal-evidence relation."""

    source: str
    target: str
    relation: str


class DecisionTrace:
    """A compact event view where tool actions and their results form episodes."""

    def __init__(
        self,
        source: EventSequence | None = None,
        *,
        nodes: Sequence[DecisionNode] | None = None,
        edges: Sequence[DecisionEdge] = (),
        source_graph: WorkGraph | None = None,
        session_id: str | None = None,
    ) -> None:
        if source is not None:
            if nodes is not None or edges or source_graph is not None:
                raise ValueError("Pass a thread or projected trace fields, not both")
            trace = type(self).from_events(
                source.selected_events,
                session_id=getattr(source, "session_id", None),
            )
            self.__dict__.update(trace.__dict__)
            return
        if source_graph is None:
            raise ValueError("source_graph is required when constructing from projected trace fields")
        self.nodes = tuple(nodes or ())
        self.edges = tuple(edges)
        self.source_graph = source_graph
        self.session_id = session_id

    @classmethod
    def from_events(
        cls,
        events: Sequence[Event | Mapping[str, Any]],
        *,
        session_id: str | None = None,
    ) -> DecisionTrace:
        """Create the deterministic trace, reusing WorkGraph redaction and event filtering."""
        source_graph = WorkGraph.from_events(events, session_id=session_id)
        event_records = [
            Event.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
            for item in events
        ]
        by_id = {event.id: event for event in event_records}
        visible_nodes = [node for node in source_graph.nodes if node.kind != "signal"]
        graph_node_by_event = {node.event_ids[0]: node for node in visible_nodes if node.event_ids}

        def stream_key(event: Event) -> tuple[str, str, str, str]:
            return event.source_id, event.session_id, event.run_id, event.call_id or ""

        calls: dict[tuple[str, str, str, str], str] = {}
        results: dict[tuple[str, str, str, str], str] = {}
        for event_id in graph_node_by_event:
            event = by_id[event_id]
            if not event.call_id:
                continue
            if event.kind == "tool_call":
                calls[stream_key(event)] = event_id
            elif event.kind == "tool_result":
                results[stream_key(event)] = event_id

        result_for_call = {call_id: results[key] for key, call_id in calls.items() if key in results}
        call_for_result = {result_id: call_id for call_id, result_id in result_for_call.items()}
        trace_node_for_event: dict[str, str] = {}
        nodes: list[DecisionNode] = []
        event_order = {event.id: index for index, event in enumerate(event_records)}

        for source_node in visible_nodes:
            if not source_node.event_ids:
                continue
            event_id = source_node.event_ids[0]
            if event_id in call_for_result:
                continue
            event = by_id[event_id]
            related_result_id = result_for_call.get(event_id)
            related_result = by_id.get(related_result_id) if related_result_id else None
            trace_id = f"step:{event_id}"
            trace_node_for_event[event_id] = trace_id
            event_ids = (event_id, related_result_id) if related_result_id else (event_id,)
            if event.kind == "message" and event.role == "user":
                kind = "request"
                summary = source_node.summary
            elif event.kind == "message" and event.role == "assistant":
                kind = "response"
                summary = source_node.summary
            elif event.kind == "thinking":
                kind = "recorded_note"
                summary = source_node.summary
            elif event.kind == "tool_call":
                kind = "action"
                summary = source_node.summary
                if related_result_id:
                    result_node = graph_node_by_event[related_result_id]
                    result_status = related_result.status if related_result else None
                    summary += f" → {result_node.summary}"
                else:
                    result_status = None
            elif event.kind == "tool_result":
                kind = "observation"
                summary = source_node.summary
                result_status = event.status
            else:
                kind = "event"
                summary = source_node.summary
                result_status = event.status
            if event.kind not in {"tool_call", "tool_result"}:
                result_status = event.status
            nodes.append(DecisionNode(
                id=trace_id,
                kind=kind,
                summary=summary,
                event_ids=event_ids,
                timestamp=event.timestamp,
                completed_at=related_result.timestamp if related_result else None,
                status=result_status,
            ))
            if related_result_id:
                trace_node_for_event[related_result_id] = trace_id

        # Keep detected behavioral patterns as sidecar nodes, linked to their event evidence.
        signal_node_ids: set[str] = set()
        for signal in (node for node in source_graph.nodes if node.kind == "signal"):
            nodes.append(DecisionNode(signal.id, "signal", signal.summary, signal.event_ids))
            signal_node_ids.add(signal.id)

        # Sequence action episodes by their first recorded event, preserving stream boundaries.
        streams: dict[tuple, list[DecisionNode]] = defaultdict(list)
        for node in nodes:
            if node.kind == "signal" or not node.event_ids:
                continue
            event = by_id[node.event_ids[0]]
            streams[_stream(event)].append(node)
        edges: list[DecisionEdge] = []
        for stream_nodes in streams.values():
            ordered = sorted(stream_nodes, key=lambda node: event_order[node.event_ids[0]])
            edges.extend(
                DecisionEdge(left.id, right.id, "then")
                for left, right in pairwise(ordered)
            )

        # Preserve observed parentage and signal evidence, while action-result links
        # are represented inside the combined action episode itself.
        for edge in source_graph.edges:
            if edge.relation == "next" or edge.relation == "returned":
                continue
            source_id = edge.source.removeprefix("event:")
            target_id = edge.target.removeprefix("event:")
            source_trace = trace_node_for_event.get(source_id, edge.source if edge.source in signal_node_ids else None)
            target_trace = trace_node_for_event.get(target_id, edge.target if edge.target in signal_node_ids else None)
            if source_trace and target_trace and source_trace != target_trace:
                edges.append(DecisionEdge(source_trace, target_trace, edge.relation))

        return cls(nodes=nodes, edges=edges, source_graph=source_graph, session_id=session_id)

    @classmethod
    def from_trajectory(cls, trajectory: Trajectory) -> DecisionTrace:
        return cls.from_events(trajectory.selected_events, session_id=getattr(trajectory, "session_id", None))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_event_count": self.source_graph.source_event_count,
            "omitted_event_count": self.source_graph.omitted_event_count,
            "event_stream_count": self.source_graph.event_stream_count,
            "nodes": [node.__dict__ for node in self.nodes],
            "edges": [edge.__dict__ for edge in self.edges],
        }

    def to_markdown(self) -> str:
        lines = [
            "## DecisionTrace (observable event sequence)",
            (f"Session `{self.session_id or 'unknown'}`; {self.source_graph.source_event_count} source events, "
             f"{self.source_graph.omitted_event_count} omitted as system context or duplicate records."),
            "This trace records actions and outcomes. It does not reconstruct hidden reasoning.",
            "",
        ]
        if self.source_graph.event_stream_count > 1:
            lines.append("No single conversation overview is shown because this input contains multiple streams.")
        else:
            lines.append("### Conversation overview")
            for label, item in (
                ("First user prompt", self.source_graph.overview.first_user_prompt),
                ("First agent text response", self.source_graph.overview.first_agent_response),
                ("Last agent text response", self.source_graph.overview.last_agent_response),
            ):
                lines.append(
                    f"- {label}: `{item.event_id}` {_compact(item.text, 320)}" if item
                    else f"- {label}: no recorded message"
                )
        lines.append("")
        lines.extend(
            f"- `{node.id}` [{node.kind}] {node.summary} "
            f"(events: {', '.join(node.event_ids)}"
            f"{f'; status: {node.status}' if node.status else ''}"
            f"{f'; completed: {node.completed_at}' if node.completed_at else ''})"
            for node in self.nodes
        )
        if self.edges:
            lines.extend(["", "Relations:"])
            lines.extend(f"- `{edge.source}` --{edge.relation}--> `{edge.target}`" for edge in self.edges)
        lines.append(
            "\nSource expansion uses `expand_source_events(event_ids, max_chars)`; "
            "request only event IDs needed to verify a conclusion."
        )
        return "\n".join(lines)

    def to_mermaid(self, *, direction: str = "LR") -> str:
        if direction not in {"LR", "RL", "TB", "BT"}:
            raise ValueError("direction must be LR, RL, TB, or BT")
        ids = {node.id: f"d{index}" for index, node in enumerate(self.nodes)}
        lines = [f"flowchart {direction}"]
        overview_items = (
            ("d_overview_prompt", "First user prompt", self.source_graph.overview.first_user_prompt),
            ("d_overview_first", "First agent response", self.source_graph.overview.first_agent_response),
            ("d_overview_last", "Last agent response", self.source_graph.overview.last_agent_response),
        )
        ordered_overview: list[tuple[str, str]] = []
        if any(item for _, _, item in overview_items):
            lines.append('    subgraph conversation_overview["Conversation overview"]')
            for node_id, label, item in overview_items:
                if item is None:
                    continue
                detail = _compact(item.text, 220)
                content = _mermaid_label(f"{label}: {detail} (event:{item.event_id})")
                lines.append(f'        {node_id}["{content}"]')
                if not ordered_overview or item.event_id != ordered_overview[-1][1]:
                    ordered_overview.append((node_id, item.event_id))
            for (earlier, _), (later, _) in pairwise(ordered_overview):
                lines.append(f"        {earlier} -.-> {later}")
            lines.append("    end")
        for node in self.nodes:
            label = f"{node.kind}: {node.summary}"
            if node.status:
                label += f"\nstatus: {node.status}"
            if node.completed_at:
                label += f"\ncompleted: {node.completed_at}"
            label += f"\nevents: {', '.join(node.event_ids)}"
            lines.append(f'    {ids[node.id]}["{_mermaid_label(label)}"]')
        for edge in self.edges:
            lines.append(f'    {ids[edge.source]} -->|"{edge.relation}"| {ids[edge.target]}')
        for kind, color in (
            ("request", "#e8f1ff"), ("response", "#efe8ff"), ("action", "#e8f7ef"),
            ("observation", "#fff7e6"), ("recorded_note", "#f1f1f1"), ("signal", "#fff0cc"),
        ):
            lines.append(f"    classDef {kind} fill:{color},stroke:#596579,color:#1f2933")
            lines.extend(f"    class {ids[node.id]} {kind}" for node in self.nodes if node.kind == kind)
        lines.append("    classDef overview fill:#f1edfa,stroke:#78679b,color:#302b3a")
        lines.extend(f"    class {node_id} overview" for node_id, _, item in overview_items if item is not None)
        return "\n".join(lines) + "\n"

    def expand_source_events(self, event_ids: Sequence[str], *, max_chars: int = 6000) -> str:
        return self.source_graph.expand_source_events(event_ids, max_chars=max_chars)

    def expansion_tool(self):
        return self.source_graph.expansion_tool()

    def render_png(self, filename: str | Path, *, direction: str = "LR", timeout: float = 60) -> Path:
        return render_mermaid_png(self.to_mermaid(direction=direction), filename, timeout=timeout)
