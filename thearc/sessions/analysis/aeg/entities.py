"""Step 4: a unified execution graph, steps linked to the entities they touch (files, tools, models, projects).

Kept as a bipartite edge list, ``(case, state index, kind, entity)``, which answers the questions a hypergraph would:
which files are edited again and again across sessions, which files sit inside failure loops, which models get
stuck on which tools, which projects fail most.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .graph import CaseSequence
from .mining import LoopInstance


@dataclass(frozen=True)
class Link:
    case_id: str
    state: int  # Index into the case's states.
    kind: str  # file, tool, model, project
    entity: str
    action: str  # The action's family: EDIT, EXPLORE, TEST, …
    failed: bool


def links(sequences: Sequence[CaseSequence]) -> list[Link]:
    """Every step's links to the entities it touched."""
    found: list[Link] = []
    for sequence in sequences:
        case = sequence.case
        for index, state in enumerate(sequence.states):
            for action in state.actions:
                failed = action.outcome == "fail"
                for path in action.paths:
                    found.append(Link(case.case_id, index, "file", path, action.family, failed))
                if action.tool:
                    found.append(Link(case.case_id, index, "tool", action.tool, action.family, failed))
                if action.model:
                    found.append(Link(case.case_id, index, "model", action.model, action.family, failed))
            if case.cwd:
                found.append(Link(case.case_id, index, "project", case.cwd, "", False))
    return found


def entity_report(sequences: Sequence[CaseSequence], instances: Sequence[LoopInstance], *,
                  top: int = 15) -> dict[str, list[dict[str, Any]]]:  # fmt: skip
    """Edit hotspots, files in failure loops, models stuck on tools, and projects by completion rate."""
    edges = links(sequences)
    session_of = {sequence.case.case_id: sequence.case.session_id for sequence in sequences}
    in_loop: defaultdict[str, set[int]] = defaultdict(set)
    for instance in instances:
        if instance.failed or any("fail" in name for name in instance.cycle):
            in_loop[instance.case_id].update(range(instance.start, instance.end))

    edits: Counter[str] = Counter()
    edit_sessions: defaultdict[str, set[str]] = defaultdict(set)
    loop_files: Counter[str] = Counter()
    loop_cases: defaultdict[str, set[str]] = defaultdict(set)
    model_tool: defaultdict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    tools_by_step: defaultdict[tuple[str, int], list[Link]] = defaultdict(list)
    for link in edges:
        if link.kind == "file":
            if link.action == "EDIT":
                edits[link.entity] += 1
                edit_sessions[link.entity].add(session_of[link.case_id])
            if link.state in in_loop.get(link.case_id, ()):
                loop_files[link.entity] += 1
                loop_cases[link.entity].add(link.case_id)
        elif link.kind in {"tool", "model"}:
            tools_by_step[(link.case_id, link.state)].append(link)
    for (case_id, state), step in tools_by_step.items():
        models = {link.entity for link in step if link.kind == "model"} or {"unknown"}
        for tool in (link for link in step if link.kind == "tool"):
            for model in models:
                counts = model_tool[(model, tool.entity)]
                counts[0] += 1
                counts[1] += tool.failed
                counts[2] += state in in_loop.get(case_id, ())
    projects: defaultdict[str, list[bool]] = defaultdict(list)
    for sequence in sequences:
        projects[sequence.case.cwd or "unknown"].append(sequence.success)

    hotspots = sorted(edits, key=lambda path: (-len(edit_sessions[path]), -edits[path]))[:top]
    stuck: list[dict[str, Any]] = [
        {
            "model": model,
            "tool": tool,
            "calls": calls,
            "fail_rate": round(failed / calls, 3),
            "in_failure_loops": round(looped / calls, 3),
        }
        for (model, tool), (calls, failed, looped) in model_tool.items()
        if calls >= 5
    ]
    stuck.sort(key=lambda row: (-row["in_failure_loops"], -row["fail_rate"], -row["calls"]))
    project_rows: list[dict[str, Any]] = [
        {"project": project, "cases": len(results), "completed": round(sum(results) / len(results), 3)}
        for project, results in projects.items()
        if len(results) >= 2
    ]
    project_rows.sort(key=lambda row: (row["completed"], -len(projects[row["project"]])))
    return {
        "edit_hotspots": [
            {"file": path, "edits": edits[path], "sessions": len(edit_sessions[path])} for path in hotspots
        ],
        "failure_loop_files": [
            {"file": path, "steps": count, "cases": len(loop_cases[path])}
            for path, count in loop_files.most_common(top)
        ],
        "model_tools": stuck[:top],
        "projects": project_rows[:top],
    }
