"""Corpus-level weighted activity transitions with source-trace examples."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..models import Event
from ..session import Session
from .workgraph import WorkGraph, _mermaid_label, render_mermaid_png

CaseScope = Literal["session", "trajectory", "thread"]


@dataclass(frozen=True)
class TraceExample:
    """A source-linked observed pattern occurrence."""

    case_id: str
    session_id: str
    event_ids: tuple[str, ...]
    run_id: str
    boundary: str | None = None


@dataclass(frozen=True)
class WeightedActivity:
    key: str
    occurrences: int
    cases: int


@dataclass(frozen=True)
class WeightedTransition:
    source: str
    target: str
    occurrences: int
    cases: int
    examples: tuple[TraceExample, ...]


@dataclass(frozen=True)
class WeightedPattern:
    """A weighted contiguous activity sequence of any configured length."""

    activities: tuple[str, ...]
    occurrences: int
    cases: int
    examples: tuple[TraceExample, ...]


@dataclass(frozen=True)
class WeightedWalk:
    """One case's activities in order, within one recorded run stream: (activity, event ID, timestamp) per step."""

    case_id: str
    session_id: str
    run_id: str
    boundary: str | None
    steps: tuple[tuple[str, str, str | None], ...]


@dataclass
class _Counts:
    occurrences: int = 0
    cases: set[str] = field(default_factory=set)
    examples: list[TraceExample] = field(default_factory=list)


def _activity(event: Event) -> str:
    if event.kind == "message" and event.role == "user":
        return "request"
    if event.kind == "message" and event.role == "assistant":
        return "response"
    if event.kind == "thinking":
        return "recorded_note"
    if event.kind == "tool_call":
        name = event.action_kind or event.tool_name or "unknown"
        return f"action:{name}"
    if event.kind == "tool_result":
        return f"result:{event.status or 'unreported'}"
    return event.kind


class WeightedPatternView:
    """Weighted contiguous activity patterns derived from indexed sessions.

    Pattern lengths count adjacent normalized activities within one recorded run
    stream. Occurrences count matching windows; cases count distinct sessions or
    trajectories or threads containing a pattern. Examples retain bounded source event IDs.
    """

    def __init__(
        self,
        source: Iterable[Any] | None = None,
        *,
        case_scope: CaseScope = "session",
        case_count: int = 0,
        activities: Iterable[WeightedActivity] = (),
        patterns: Iterable[WeightedPattern] = (),
        pattern_lengths: tuple[int, ...] = (2,),
        omitted_event_count: int = 0,
        boundary_counts: dict[str, int] | None = None,
        examples_per_edge: int = 3,
        walks: Iterable[WeightedWalk] = (),
    ) -> None:
        if source is not None:
            view = type(self).from_sessions(
                source,
                case_scope=case_scope,
                pattern_lengths=pattern_lengths,
                examples_per_edge=examples_per_edge,
            )
            self.__dict__.update(view.__dict__)
            return
        self.case_scope = case_scope
        self.case_count = case_count
        self.activities = tuple(activities)
        self.patterns = tuple(patterns)
        self.pattern_lengths = pattern_lengths
        self.transitions = tuple(
            WeightedTransition(
                source=pattern.activities[0],
                target=pattern.activities[1],
                occurrences=pattern.occurrences,
                cases=pattern.cases,
                examples=pattern.examples,
            )
            for pattern in self.patterns
            if len(pattern.activities) == 2
        )
        self.omitted_event_count = omitted_event_count
        self.boundary_counts = dict(boundary_counts or {})
        self.walks = tuple(walks)  # Every case's activity sequence, per run stream (for graph queries).

    @classmethod
    def from_sessions(
        cls,
        sessions: Iterable[Session],
        *,
        case_scope: CaseScope = "session",
        pattern_lengths: tuple[int, ...] = (2,),
        examples_per_edge: int = 3,
    ) -> WeightedPatternView:
        """Build a deterministic view, streaming one session at a time."""
        if case_scope not in {"session", "trajectory", "thread"}:
            raise ValueError("case_scope must be session, trajectory, or thread")
        requested_lengths = tuple(pattern_lengths)
        if not requested_lengths or any(
            not isinstance(length, int) or isinstance(length, bool) or length < 2
            for length in requested_lengths
        ):
            raise ValueError("pattern_lengths must contain one or more integers >= 2")
        pattern_lengths = tuple(sorted(set(requested_lengths)))
        if examples_per_edge < 0:
            raise ValueError("examples_per_edge must be non-negative")

        activity_counts: dict[str, _Counts] = defaultdict(_Counts)
        pattern_counts: dict[tuple[str, ...], _Counts] = defaultdict(_Counts)
        cases: set[str] = set()
        boundary_counts: dict[str, int] = defaultdict(int)
        omitted_event_count = 0
        walks: list[WeightedWalk] = []

        for session in sessions:
            if case_scope in {"session", "thread"}:
                boundary = "thread" if case_scope == "thread" else "session"
                cases_for_session = [(session.id, session.selected_events, boundary)]
            else:
                cases_for_session = [
                    (trajectory.id, trajectory.selected_events, trajectory.boundary)
                    for trajectory in session.trajectories
                ]
                if session.trajectories.unassigned_events:
                    cases_for_session.append((
                        f"unassigned:{session.id}",
                        session.trajectories.unassigned_events,
                        "unassigned",
                    ))

            for case_id, case_events, boundary in cases_for_session:
                cases.add(case_id)
                boundary_counts[boundary] += 1
                graph = WorkGraph.from_events(
                    case_events,
                    trajectory_id=case_id if case_scope == "trajectory" else None,
                    session_id=getattr(session, "session_id", session.id),
                    boundary=boundary if case_scope == "trajectory" else None,
                )
                omitted_event_count += graph.omitted_event_count
                event_by_id = {
                    event.id: Event.model_validate(event.model_dump()) for event in case_events
                }
                streams: dict[tuple[str, str, str], list[tuple[Event, str]]] = defaultdict(list)
                for node in graph.nodes:
                    if node.kind == "signal" or not node.event_ids:
                        continue
                    event = event_by_id[node.event_ids[0]]
                    key = _activity(event)
                    streams[(event.source_id, event.session_id, event.run_id)].append((event, key))
                    count = activity_counts[key]
                    count.occurrences += 1
                    count.cases.add(case_id)

                for (_source_id, session_id, run_id), stream in streams.items():
                    walks.append(WeightedWalk(case_id, session_id, run_id, boundary,
                                              tuple((key, event.id, event.timestamp) for event, key in stream)))
                    for pattern_length in pattern_lengths:
                        for start in range(len(stream) - pattern_length + 1):
                            window = stream[start : start + pattern_length]
                            activities_in_window = tuple(activity for _, activity in window)
                            counts = pattern_counts[activities_in_window]
                            counts.occurrences += 1
                            counts.cases.add(case_id)
                            sampled_cases = {example.case_id for example in counts.examples}
                            if len(counts.examples) < examples_per_edge and case_id not in sampled_cases:
                                counts.examples.append(TraceExample(
                                    case_id=case_id,
                                    session_id=session_id,
                                    event_ids=tuple(event.id for event, _ in window),
                                    run_id=run_id,
                                    boundary=boundary,
                                ))

        activities = [
            WeightedActivity(key, counts.occurrences, len(counts.cases))
            for key, counts in sorted(activity_counts.items())
        ]
        patterns = [
            WeightedPattern(sequence, counts.occurrences, len(counts.cases), tuple(counts.examples))
            for sequence, counts in sorted(pattern_counts.items(), key=lambda item: (len(item[0]), item[0]))
        ]
        return cls(
            case_scope=case_scope,
            case_count=len(cases),
            activities=activities,
            patterns=patterns,
            pattern_lengths=pattern_lengths,
            omitted_event_count=omitted_event_count,
            boundary_counts=dict(sorted(boundary_counts.items())),
            walks=walks,
        )

    @classmethod
    def from_threads(
        cls,
        threads: Iterable[Any],
        *,
        pattern_lengths: tuple[int, ...] = (2,),
        examples_per_edge: int = 3,
    ) -> WeightedPatternView:
        """Build one weighted-pattern case per execution thread."""
        return cls.from_sessions(
            threads,
            case_scope="thread",
            pattern_lengths=pattern_lengths,
            examples_per_edge=examples_per_edge,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_scope": self.case_scope,
            "case_count": self.case_count,
            "pattern_lengths": self.pattern_lengths,
            "omitted_event_count": self.omitted_event_count,
            "boundary_counts": self.boundary_counts,
            "activities": [activity.__dict__ for activity in self.activities],
            "patterns": [
                {
                    "activities": pattern.activities,
                    "occurrences": pattern.occurrences,
                    "cases": pattern.cases,
                    "examples": [example.__dict__ for example in pattern.examples],
                }
                for pattern in self.patterns
            ],
            "transitions": [
                {
                    **{key: value for key, value in transition.__dict__.items() if key != "examples"},
                    "examples": [example.__dict__ for example in transition.examples],
                }
                for transition in self.transitions
            ],
        }

    def to_markdown(self, *, limit: int = 100) -> str:
        ranked = sorted(
            self.patterns,
            key=lambda pattern: (-pattern.cases, -pattern.occurrences, len(pattern.activities), pattern.activities),
        )
        lines = [
            "# WeightedPatternView",
            (
                f"Case scope: `{self.case_scope}`; {self.case_count} cases; {self.omitted_event_count} events "
                "omitted by the WorkGraph projection."
            ),
            (
                f"Observed contiguous patterns of lengths {self.pattern_lengths}. Case support counts each case once "
                "per pattern; occurrences count every matching window. This is descriptive, not causal."
            ),
            "",
            "| Pattern | Cases | Occurrences | Example event IDs |",
            "|---|---:|---:|---|",
        ]
        for pattern in ranked[:limit]:
            label = " → ".join(pattern.activities).replace("|", r"\|")
            example_links = ", ".join(
                f"`{example.case_id[:12]}:{'→'.join(example.event_ids)}`"
                for example in pattern.examples
            )
            lines.append(
                f"| `{label}` | {pattern.cases}/{self.case_count} | "
                f"{pattern.occurrences} | {example_links} |"
            )
        return "\n".join(lines) + "\n"

    def to_mermaid(self, *, limit: int = 80) -> str:
        ranked = sorted(
            self.patterns,
            key=lambda pattern: (-pattern.cases, -pattern.occurrences, len(pattern.activities), pattern.activities),
        )
        selected = ranked[:limit]
        pairs = [pattern for pattern in selected if len(pattern.activities) == 2]
        higher_order = [pattern for pattern in selected if len(pattern.activities) > 2]
        keys = sorted({activity for pattern in pairs for activity in pattern.activities})
        ids = {key: f"a{index}" for index, key in enumerate(keys)}
        lines = ["flowchart LR"]
        lines.extend(f'    {ids[key]}["{_mermaid_label(key)}"]' for key in keys)
        for pattern in pairs:
            label = f"{pattern.cases} cases · {pattern.occurrences} occurrences"
            source, target = pattern.activities
            lines.append(f'    {ids[source]} -->|"{_mermaid_label(label)}"| {ids[target]}')
        if higher_order:
            lines.append('    subgraph higher_order["Higher-order patterns"]')
            for index, pattern in enumerate(higher_order):
                label = " → ".join(pattern.activities)
                weight = f"{pattern.cases} cases · {pattern.occurrences} occurrences"
                lines.append(
                    f'        p{index}["{_mermaid_label(label)}<br/>{_mermaid_label(weight)}"]'
                )
            lines.append("    end")
        return "\n".join(lines) + "\n"

    def render_png(self, filename: str | Path, *, limit: int = 80, timeout: float = 60) -> Path:
        return render_mermaid_png(self.to_mermaid(limit=limit), filename, timeout=timeout)


def export_weighted_pattern_view(
    view: WeightedPatternView,
    output_dir: str | Path,
    *,
    png: bool = False,
) -> Path:
    """Write JSON, Mermaid, Markdown, and a browser viewer for the aggregate."""
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    mermaid = view.to_mermaid()
    (output / "weighted-patterns.mmd").write_text(mermaid, encoding="utf-8")
    (output / "weighted-patterns.md").write_text(view.to_markdown(), encoding="utf-8")
    (output / "weighted-patterns.json").write_text(
        json.dumps(view.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if png:
        view.render_png(output / "weighted-patterns.png")
    diagram = html.escape(mermaid, quote=False)
    title = "WeightedPatternView"
    page = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{title}</title><style>body{{font:15px/1.5 system-ui,sans-serif;margin:2rem;color:#18212b}}
.graph{{overflow:auto;background:#fff;border:1px solid #d9e0e8;padding:1rem;border-radius:8px}}</style>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad:true,securityLevel:"strict",theme:"neutral"}});</script>
<h1>{title}</h1><p>{view.case_count} {view.case_scope} cases; edges label case support and raw occurrence count.</p>
<div class="graph"><pre class="mermaid">{diagram}</pre></div>
<p><a href="weighted-patterns.md">Markdown table</a> · <a href="weighted-patterns.json">JSON data</a> · 
<a href="weighted-patterns.mmd">Mermaid source</a></p></html>
"""
    (output / "index.html").write_text(page, encoding="utf-8")
    return output
