"""Step 3: per-case sequences START → s1 → … → END:<outcome>, aggregated into one directed state graph.

Outcomes are heuristic, read from the actions (so they do not depend on the abstraction):

* ``error``            an API error among the last steps;
* ``user_aborted``     the case ends on the user's abort or correction;
* ``branch_abandoned`` a branch of a tree-shaped session that was left for another;
* ``completed``        a final summary (or the user's approval) at the end, with no failing check after the last edit;
* ``abandoned``        anything else: it ends after a failure, or without a final answer.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import Case
from .labels import Action
from .states import State

START, END = "START", "END"
SUCCESS = "completed"
FAILURES = ("abandoned", "user_aborted", "error", "branch_abandoned")
_CHECKS = {"TEST", "BUILD", "RUN"}
_QUIET = {"THINK", "MODEL", "EXPLAIN", "COMPACT"}


def outcome(case: Case, actions: list[Action]) -> str:
    """How the case ended, by the heuristics above."""
    meaningful = [action for action in actions if action.family not in _QUIET]
    if any(action.family == "API_ERROR" for action in meaningful[-3:]):
        return "error"
    if case.abandoned_branch:
        return "branch_abandoned"
    if not meaningful:
        return "abandoned"
    last = meaningful[-1]
    if last.family == "USER" and last.label in {"USER:ABORT", "USER:CORRECTION"}:
        return "user_aborted"
    if last.label == "USER:APPROVE":
        return SUCCESS
    if last.family != "FINAL_SUMMARY":
        return "abandoned"
    last_edit = max((i for i, action in enumerate(meaningful) if action.family == "EDIT"), default=-1)
    checks = [action for action in meaningful[last_edit + 1 :] if action.family in _CHECKS and action.outcome]
    return "abandoned" if checks and checks[-1].outcome == "fail" else SUCCESS


@dataclass
class CaseSequence:
    """A case's collapsed states, from START to its END state."""

    case: Case
    actions: list[Action]
    states: list[State]
    outcome: str

    @property
    def names(self) -> list[str]:
        return [START, *(state.name for state in self.states), f"{END}:{self.outcome}"]

    @property
    def success(self) -> bool:
        return self.outcome == SUCCESS


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds(start: str | None, end: str | None) -> float | None:
    first, second = _time(start), _time(end)
    if first is None or second is None:
        return None
    try:
        return max(0.0, (second - first).total_seconds())
    except TypeError:  # One timestamp has a timezone and the other does not.
        return None


@dataclass
class Node:
    name: str
    visits: int = 0
    cases: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    repeats: list[int] = field(default_factory=list)
    tokens: list[int] = field(default_factory=list)
    dwell: list[float] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "visits": self.visits,
            "cases": len(self.cases),
            "sessions": len(self.sessions),
            "avg_repeat": round(statistics.fmean(self.repeats), 2) if self.repeats else 0,
            "avg_tokens": round(statistics.fmean(self.tokens)) if self.tokens else 0,
            "avg_dwell_seconds": round(statistics.fmean(self.dwell), 1) if self.dwell else None,
            "samples": self.samples,
        }


@dataclass
class Edge:
    source: str
    target: str
    count: int = 0
    probability: float = 0.0
    durations: list[float] = field(default_factory=list)
    case_outcomes: dict[str, str] = field(default_factory=dict)
    loop_backs: int = 0
    iterations: list[int] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        outcomes = list(self.case_outcomes.values())
        return sum(value == SUCCESS for value in outcomes) / len(outcomes) if outcomes else 0.0

    @property
    def loop_back(self) -> bool:
        """The target was already visited earlier in the case, in most traversals: an edge closing a loop."""
        return self.loop_backs * 2 > self.count

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "count": self.count,
            "probability": round(self.probability, 4),
            "median_seconds": round(statistics.median(self.durations), 1) if self.durations else None,
            "cases": len(self.case_outcomes),
            "success_rate": round(self.success_rate, 3),
            "loop_back": self.loop_back,
            "avg_loop_iterations": round(statistics.fmean(self.iterations), 2) if self.iterations else None,
        }


@dataclass
class StateGraph:
    nodes: dict[str, Node]
    edges: dict[tuple[str, str], Edge]

    def outgoing(self, name: str) -> list[Edge]:
        return [edge for (source, _), edge in self.edges.items() if source == name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in sorted(self.nodes.values(), key=lambda n: -n.visits)],
            "edges": [edge.to_dict() for edge in sorted(self.edges.values(), key=lambda e: -e.count)],
        }


def aggregate(sequences: list[CaseSequence], *, samples: int = 8) -> StateGraph:
    """One directed graph of abstract states over every case."""
    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str], Edge] = {}
    for sequence in sequences:
        case = sequence.case
        names = sequence.names
        timed: list[State | None] = [None, *sequence.states, None]
        seen: set[str] = set()
        traversals: Counter[tuple[str, str]] = Counter()
        for position, name in enumerate(names):
            node = nodes.setdefault(name, Node(name))
            node.visits += 1
            node.cases.add(case.case_id)
            node.sessions.add(case.session_id)
            state = timed[position]
            if state is not None:
                node.repeats.append(state.repeat)
                node.tokens.append(state.tokens)
                following = timed[position + 1] if position + 1 < len(timed) else None
                if following is not None and (dwell := _seconds(state.start, following.start)) is not None:
                    node.dwell.append(dwell)
                for action in state.actions:
                    if action.sample and action.sample not in node.samples and len(node.samples) < samples:
                        node.samples.append(action.sample)
            if position + 1 < len(names):
                target = names[position + 1]
                edge = edges.setdefault((name, target), Edge(name, target))
                edge.count += 1
                edge.case_outcomes[case.case_id] = sequence.outcome
                if target in seen or target == name:
                    edge.loop_backs += 1
                after = timed[position + 1] if position + 1 < len(timed) else None
                if state is not None and after is not None and (seconds := _seconds(state.start, after.start)):
                    edge.durations.append(seconds)
                traversals[(name, target)] += 1
            seen.add(name)
        for key, times in traversals.items():
            if edges[key].loop_backs and times > 1:
                edges[key].iterations.append(times)
    totals: defaultdict[str, int] = defaultdict(int)
    for (source, _), edge in edges.items():
        totals[source] += edge.count
    for (source, _), edge in edges.items():
        edge.probability = edge.count / totals[source]
    return StateGraph(nodes, edges)
