"""Step 5 and 5b: patterns mined from the case sequences, and metrics of the aggregated graph.

Loop and success numbers come from the per-case sequences, never from the merged graph alone: the merged graph
loses each case's history, and a path through it may never have happened.
"""

from __future__ import annotations

import heapq
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from .graph import END, FAILURES, START, SUCCESS, CaseSequence, StateGraph

# --- variants --------------------------------------------------------------------------------------------------


def variants(sequences: Sequence[CaseSequence], *, top: int = 15) -> list[dict[str, Any]]:
    """The most common whole sequences, with the share of cases the top ones cover together."""
    counts = Counter(tuple(sequence.names) for sequence in sequences)
    total, covered, rows = len(sequences), 0, []
    for names, count in counts.most_common(top):
        covered += count
        rows.append({"variant": list(names), "cases": count, "share": round(count / total, 4),
                     "cumulative": round(covered / total, 4)})  # fmt: skip
    return rows


# --- frequent subsequences (PrefixSpan) --------------------------------------------------------------------------


def prefixspan(sequences: Sequence[Sequence[str]], *, min_support: int, max_length: int = 4,
               limit: int = 2000) -> list[tuple[tuple[str, ...], int]]:  # fmt: skip
    """Frequent subsequences (gaps allowed) and their support, the number of sequences containing them.

    Projection-based PrefixSpan; ``limit`` stops the search on very dense data.
    """
    found: list[tuple[tuple[str, ...], int]] = []

    def grow(prefix: tuple[str, ...], projected: list[tuple[int, int]]) -> None:
        if len(found) >= limit or len(prefix) >= max_length:
            return
        support: dict[str, set[int]] = defaultdict(set)
        for sequence_index, start in projected:
            for item in set(sequences[sequence_index][start:]):
                support[item].add(sequence_index)
        for item, members in sorted(support.items()):
            if len(members) < min_support:
                continue
            pattern = (*prefix, item)
            found.append((pattern, len(members)))
            next_projection = []
            for sequence_index, start in projected:
                if sequence_index not in members:
                    continue
                sequence = sequences[sequence_index]
                try:
                    position = sequence.index(item, start)
                except ValueError:
                    continue
                next_projection.append((sequence_index, position + 1))
            grow(pattern, next_projection)

    grow((), [(index, 0) for index in range(len(sequences))])
    return found


def frequent_subsequences(sequences: Sequence[CaseSequence], *, min_support: float = 0.03, max_length: int = 4,
                          top: int = 20, cap: int = 150) -> list[dict[str, Any]]:  # fmt: skip
    """Closed frequent subsequences of length ≥ 2: those no longer pattern with the same support contains."""
    data = [[name for name in sequence.names[1:-1]][:cap] for sequence in sequences]
    threshold = max(2, math.ceil(min_support * len(data)))
    patterns = [(p, s) for p, s in prefixspan(data, min_support=threshold, max_length=max_length) if len(p) >= 2]

    def contains(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
        iterator = iter(longer)
        return all(item in iterator for item in shorter)

    by_support: defaultdict[int, list[tuple[str, ...]]] = defaultdict(list)
    for pattern, support in patterns:
        by_support[support].append(pattern)
    closed = [
        (pattern, support)
        for pattern, support in patterns
        if not any(len(other) > len(pattern) and contains(other, pattern) for other in by_support[support])
    ]
    closed.sort(key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [{"pattern": list(pattern), "cases": support, "share": round(support / len(data), 4)}
            for pattern, support in closed[:top]]  # fmt: skip


# --- loops -------------------------------------------------------------------------------------------------------


@dataclass
class LoopInstance:
    case_id: str
    cycle: tuple[str, ...]  # One state for a self-loop; two or three for a short cycle.
    iterations: int
    start: int  # Index into the case's states.
    end: int  # Exclusive.
    exit: str  # The state after the loop (END:<outcome> when the case ends in it).
    failed: bool  # The case did not complete.
    tokens: int = 0


def loop_instances(sequence: CaseSequence, *, min_iterations: int = 2) -> list[LoopInstance]:
    """Self-loops (a state repeated) and short cycles (two or three states repeated in turn) in one case."""
    states = sequence.states
    names = [state.name for state in states]
    exit_of = lambda end: names[end] if end < len(names) else f"{END}:{sequence.outcome}"
    failed = not sequence.success
    found: list[LoopInstance] = []
    for index, state in enumerate(states):
        if state.repeat >= min_iterations:
            found.append(LoopInstance(sequence.case.case_id, (state.name,), state.repeat, index, index + 1,
                                      exit_of(index + 1), failed, state.tokens))  # fmt: skip
    index = 0
    while index < len(names):
        best = None
        for length in (2, 3):
            cycle = tuple(names[index : index + length])
            if len(cycle) < length or len(set(cycle)) < length:
                continue
            repeats = 1
            while tuple(names[index + repeats * length : index + (repeats + 1) * length]) == cycle:
                repeats += 1
            if repeats >= min_iterations and (best is None or repeats * length > best[1] * len(best[0])):
                best = (cycle, repeats)
        if best is None:
            index += 1
            continue
        cycle, repeats = best
        end = index + repeats * len(cycle)
        rotation = min(cycle[i:] + cycle[:i] for i in range(len(cycle)))  # One name for every rotation.
        tokens = sum(state.tokens for state in states[index:end])
        found.append(LoopInstance(sequence.case.case_id, rotation, repeats, index, end, exit_of(end), failed, tokens))
        index = end
    return found


def loops(sequences: Sequence[CaseSequence], instances: Sequence[LoopInstance], *,
          top: int = 15) -> list[dict[str, Any]]:  # fmt: skip
    """Each loop's frequency, typical length, how often it ends in failure, and P(exit at k | reached k)."""
    grouped: defaultdict[tuple[str, ...], list[LoopInstance]] = defaultdict(list)
    for instance in instances:
        grouped[instance.cycle].append(instance)
    rows: list[dict[str, Any]] = []
    for cycle, members in grouped.items():
        iterations = [member.iterations for member in members]
        reached: Counter[int] = Counter()
        for value in iterations:
            for k in range(2, value + 1):
                reached[k] += 1
        exits = Counter(iterations)
        curve = {k: round(exits[k] / reached[k], 3) for k in sorted(reached) if k <= 10 and reached[k] >= 3}
        rows.append({
            "loop": list(cycle),
            "kind": "self" if len(cycle) == 1 else "cycle",
            "instances": len(members),
            "cases": len({member.case_id for member in members}),
            "median_iterations": statistics.median(iterations),
            "max_iterations": max(iterations),
            "ends_in_failure": round(sum(m.failed and m.exit.startswith(END) for m in members) / len(members), 3),
            "in_failed_cases": round(sum(m.failed for m in members) / len(members), 3),
            "exit_probability": curve,
            "trend": _trend(curve),
            "tokens": sum(member.tokens for member in members),
            "examples": sorted({member.case_id for member in members})[:5],
        })  # fmt: skip
    rows.sort(key=lambda row: (-row["cases"], -row["instances"]))
    return rows[:top]


def _trend(curve: dict[int, float]) -> str:
    """Flat or falling exit probability after k ≥ 3: stuck, no new information; rising: productive retries."""
    later = [value for k, value in curve.items() if k >= 3]
    if len(later) < 2:
        return "too few"
    slope = later[-1] - later[0]
    return "productive retry" if slope > 0.05 else "stuck (falling)" if slope < -0.05 else "stuck (flat)"


# --- contrast ----------------------------------------------------------------------------------------------------


def _features(sequence: CaseSequence, lengths: Iterable[int] = (1, 2, 3)) -> set[tuple[str, ...]]:
    names = sequence.names[1:-1]
    return {tuple(names[i : i + n]) for n in lengths for i in range(len(names) - n + 1)}


def contrast(group: Sequence[CaseSequence], other: Sequence[CaseSequence], *, min_cases: int = 5,
             top: int = 15) -> dict[str, list[dict[str, Any]]]:  # fmt: skip
    """States and n-grams over-represented in ``group`` versus ``other``: smoothed log-odds and lift.

    ``group`` first, so with failed and completed cases the positive side lists the leading indicators of failure.
    """
    in_group = Counter(feature for sequence in group for feature in _features(sequence))
    in_other = Counter(feature for sequence in other for feature in _features(sequence))
    a_total, b_total = max(len(group), 1), max(len(other), 1)
    rows: list[dict[str, Any]] = []
    for feature in set(in_group) | set(in_other):
        a, b = in_group[feature], in_other[feature]
        if a + b < min_cases:
            continue
        odds = math.log((a + 0.5) / (a_total - a + 0.5)) - math.log((b + 0.5) / (b_total - b + 0.5))
        share = (a + b) / (a_total + b_total)
        lift = (a / a_total) / share if share else 0.0
        error = math.sqrt(1 / (a + 0.5) + 1 / (a_total - a + 0.5) + 1 / (b + 0.5) + 1 / (b_total - b + 0.5))
        rows.append({"pattern": list(feature), "group_cases": a, "other_cases": b,
                     "group_share": round(a / a_total, 3), "other_share": round(b / b_total, 3),
                     "log_odds": round(odds, 3), "z": round(odds / error, 2), "lift": round(lift, 3)})  # fmt: skip
    over = sorted((row for row in rows if row["log_odds"] > 0), key=lambda row: (-row["z"], -row["log_odds"]))
    under = sorted((row for row in rows if row["log_odds"] < 0), key=lambda row: (row["z"], row["log_odds"]))
    return {"over": over[:top], "under": under[:top]}


def outcome_contrast(sequences: Sequence[CaseSequence], **options: Any) -> dict[str, list[dict[str, Any]]]:
    """Leading indicators of failure (``over``) and of success (``under``)."""
    failed = [sequence for sequence in sequences if sequence.outcome in FAILURES]
    completed = [sequence for sequence in sequences if sequence.success]
    return contrast(failed, completed, **options)


# --- interventions, cost, drift ----------------------------------------------------------------------------------

_INTERVENTIONS = ("USER_TURN:CORRECTION", "USER_TURN:ABORT", "USER:CORRECTION", "USER:ABORT")


def interventions(sequences: Sequence[CaseSequence], graph: StateGraph, *, top: int = 15) -> list[dict[str, Any]]:
    """The states right before a user's correction or abort: where humans step in, and how often per visit."""
    before: Counter[tuple[str, str]] = Counter()
    examples: defaultdict[str, list[str]] = defaultdict(list)
    for sequence in sequences:
        names = sequence.names
        for index in range(1, len(names)):
            target = names[index]
            if target.split(" > ")[-1] in _INTERVENTIONS:
                before[(names[index - 1], target)] += 1
                if len(examples[names[index - 1]]) < 5:
                    examples[names[index - 1]].append(sequence.case.case_id)
    rows: list[dict[str, Any]] = []
    for (state, intervention), count in before.most_common(top):
        visits = graph.nodes[state].visits if state in graph.nodes else 0
        rows.append({"state": state, "intervention": intervention, "count": count,
                     "rate": round(count / visits, 3) if visits else None, "examples": examples[state]})  # fmt: skip
    return rows


def cost_hotspots(sequences: Sequence[CaseSequence], instances: Sequence[LoopInstance], *,
                  top: int = 15) -> dict[str, list[dict[str, Any]]]:  # fmt: skip
    """The states and loops that consume the most tokens."""
    tokens: Counter[str] = Counter()
    cost: Counter[str] = Counter()
    visits: Counter[str] = Counter()
    for sequence in sequences:
        for state in sequence.states:
            tokens[state.name] += state.tokens
            cost[state.name] += state.cost  # type: ignore[assignment]
            visits[state.name] += 1
    total = sum(tokens.values()) or 1
    states = [{"state": name, "tokens": value, "share": round(value / total, 4), "cost": round(cost[name], 4),
               "tokens_per_visit": round(value / visits[name])} for name, value in tokens.most_common(top)]  # fmt: skip
    by_loop: Counter[tuple[str, ...]] = Counter()
    for instance in instances:
        by_loop[instance.cycle] += instance.tokens
    loop_rows = [{"loop": list(cycle), "tokens": value, "share": round(value / total, 4)}
                 for cycle, value in by_loop.most_common(top)]  # fmt: skip
    return {"states": states, "loops": loop_rows}


def drift(sequences: Sequence[CaseSequence], instances: Sequence[LoopInstance]) -> dict[str, list[dict[str, Any]]]:
    """The headline numbers split by month and by model."""
    stuck = {instance.case_id for instance in instances if instance.iterations >= 3}

    def summary(key: str, members: list[CaseSequence]) -> dict[str, Any]:
        states = Counter(name for member in members for name in member.names[1:-1])
        total = sum(states.values()) or 1
        return {
            "key": key,
            "cases": len(members),
            "completed": round(sum(member.success for member in members) / len(members), 3),
            "median_states": statistics.median(len(member.states) for member in members),
            "loop_cases": round(sum(member.case.case_id in stuck for member in members) / len(members), 3),
            "tokens": sum(member.case.tokens for member in members),
            "top_states": [[name, round(count / total, 3)] for name, count in states.most_common(3)],
        }

    by_month: defaultdict[str, list[CaseSequence]] = defaultdict(list)
    by_model: defaultdict[str, list[CaseSequence]] = defaultdict(list)
    for sequence in sequences:
        by_month[sequence.case.month].append(sequence)
        by_model[sequence.case.model or "unknown"].append(sequence)
    return {
        "month": [summary(key, members) for key, members in sorted(by_month.items())],
        "model": [summary(key, members) for key, members in sorted(by_model.items(), key=lambda kv: -len(kv[1]))],
    }


# --- graph metrics (5b) ------------------------------------------------------------------------------------------


def happy_path(sequences: Sequence[CaseSequence], graph: StateGraph) -> dict[str, Any]:
    """The most common completed variant, and the most probable path START → END:completed through the graph
    (edge weight −log P(target | source)), flagged when no case actually took it."""
    goal = f"{END}:{SUCCESS}"
    completed = Counter(tuple(sequence.names) for sequence in sequences if sequence.success)
    observed = {tuple(sequence.names) for sequence in sequences}
    best_variant = completed.most_common(1)
    distance: dict[str, float] = {START: 0.0}
    previous: dict[str, str] = {}
    queue = [(0.0, START)]
    while queue:
        cost, name = heapq.heappop(queue)
        if name == goal:
            break
        if cost > distance.get(name, math.inf):
            continue
        for edge in graph.outgoing(name):
            weight = -math.log(edge.probability) if edge.probability > 0 else math.inf
            if cost + weight < distance.get(edge.target, math.inf):
                distance[edge.target] = cost + weight
                previous[edge.target] = name
                heapq.heappush(queue, (cost + weight, edge.target))
    path: list[str] = []
    if goal in distance:
        node = goal
        while node != START:
            path.append(node)
            node = previous[node]
        path.append(START)
        path.reverse()
    return {
        "variant": list(best_variant[0][0]) if best_variant else [],
        "variant_cases": best_variant[0][1] if best_variant else 0,
        "graph_path": path,
        "graph_path_probability": round(math.exp(-distance[goal]), 6) if goal in distance else 0.0,
        "graph_path_observed": tuple(path) in observed,
    }


def branching_entropy(sequences: Sequence[CaseSequence], graph: StateGraph, *, top: int = 15) -> list[dict[str, Any]]:
    """H(S) = −Σ P(S′|S)·log2 P(S′|S) per state, overall and within completed and failed cases.

    High entropy is a decision fork, or a state too coarse to predict what follows; split it before calling it
    confusion.
    """

    def entropy(counts: Counter[str]) -> float:
        total = sum(counts.values())
        return -sum(c / total * math.log2(c / total) for c in counts.values()) if total else 0.0

    by_outcome: dict[str, defaultdict[str, Counter[str]]] = {"completed": defaultdict(Counter),
                                                             "failed": defaultdict(Counter)}  # fmt: skip
    for sequence in sequences:
        side = "completed" if sequence.success else "failed"
        names = sequence.names
        for source, target in pairwise(names):
            by_outcome[side][source][target] += 1
    rows: list[dict[str, Any]] = []
    for name, node in graph.nodes.items():
        out = Counter({edge.target: edge.count for edge in graph.outgoing(name)})
        if not out or name.startswith(END):
            continue
        rows.append({"state": name, "visits": node.visits, "entropy": round(entropy(out), 3),
                     "weighted": round(entropy(out) * node.visits, 1),
                     "entropy_completed": round(entropy(by_outcome["completed"][name]), 3),
                     "entropy_failed": round(entropy(by_outcome["failed"][name]), 3),
                     "successors": len(out)})  # fmt: skip
    rows.sort(key=lambda row: -row["weighted"])
    return rows[:top]


def failure_sinks(sequences: Sequence[CaseSequence], *, min_cases: int = 5, top: int = 15) -> list[dict[str, Any]]:
    """Per state: completion rate of the cases passing through it against the baseline, length-adjusted (cases are
    compared with others of similar length, since long cases pass through more states), and how often it is the
    last state of a failed case."""
    if not sequences:
        return []
    lengths = sorted(len(sequence.states) for sequence in sequences)
    cuts = [lengths[int(len(lengths) * q)] for q in (0.25, 0.5, 0.75)]
    stratum = lambda n: sum(n > cut for cut in cuts)
    strata: defaultdict[int, list[CaseSequence]] = defaultdict(list)
    for sequence in sequences:
        strata[stratum(len(sequence.states))].append(sequence)
    baseline = sum(sequence.success for sequence in sequences) / len(sequences)
    failed = [sequence for sequence in sequences if not sequence.success]
    last = Counter(sequence.states[-1].name for sequence in failed if sequence.states)
    rows: list[dict[str, Any]] = []
    for name in {state.name for sequence in sequences for state in sequence.states}:
        through = [sequence for sequence in sequences if any(state.name == name for state in sequence.states)]
        if len(through) < min_cases:
            continue
        rate = sum(sequence.success for sequence in through) / len(through)
        adjusted, weight = 0.0, 0
        for members in strata.values():
            inside = [m for m in members if any(state.name == name for state in m.states)]
            outside = [m for m in members if m not in inside]
            if inside and outside:
                delta = sum(m.success for m in inside) / len(inside) - sum(m.success for m in outside) / len(outside)
                adjusted += delta * len(inside)
                weight += len(inside)
        rows.append({"state": name, "cases": len(through), "completed": round(rate, 3),
                     "baseline": round(baseline, 3), "delta": round(rate - baseline, 3),
                     "adjusted_delta": round(adjusted / weight, 3) if weight else None,
                     "last_in_failed": last[name],
                     "last_share": round(last[name] / len(failed), 3) if failed else 0.0})  # fmt: skip
    rows.sort(key=lambda row: row["adjusted_delta"] if row["adjusted_delta"] is not None else row["delta"])
    return rows[:top]


@dataclass
class Mined:
    """Everything step 5 computed, ready for a report."""

    variants: list[dict[str, Any]]
    subsequences: list[dict[str, Any]]
    loops: list[dict[str, Any]]
    contrast: dict[str, list[dict[str, Any]]]
    baseline: dict[str, list[dict[str, Any]]] | None
    interventions: list[dict[str, Any]]
    cost: dict[str, list[dict[str, Any]]]
    drift: dict[str, list[dict[str, Any]]]
    happy_path: dict[str, Any]
    entropy: list[dict[str, Any]]
    sinks: list[dict[str, Any]]
    instances: list[LoopInstance] = field(default_factory=list, repr=False)


def mine(sequences: Sequence[CaseSequence], graph: StateGraph, *, baseline: Sequence[CaseSequence] | None = None,
         min_support: float = 0.03, top: int = 15) -> Mined:  # fmt: skip
    instances = [instance for sequence in sequences for instance in loop_instances(sequence)]
    return Mined(
        variants=variants(sequences, top=top),
        subsequences=frequent_subsequences(sequences, min_support=min_support, top=top),
        loops=loops(sequences, instances, top=top),
        contrast=outcome_contrast(sequences, top=top),
        baseline=contrast(sequences, baseline, top=top) if baseline else None,
        interventions=interventions(sequences, graph, top=top),
        cost=cost_hotspots(sequences, instances, top=top),
        drift=drift(sequences, instances),
        happy_path=happy_path(sequences, graph),
        entropy=branching_entropy(sequences, graph, top=top),
        sinks=failure_sinks(sequences, top=top),
        instances=instances,
    )
