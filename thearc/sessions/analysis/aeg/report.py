"""Step 6: outputs. Markdown, a Mermaid state graph coloured by success rate, a Mermaid Sankey of phases, and an
exported bundle: event and state tables (Parquet when ``pyarrow`` is installed, JSON Lines otherwise), an event log
for process-mining tools such as pm4py, ``graph.json``, the report, and an HTML page showing it all.
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .graph import END, START
from .states import coarsen

if TYPE_CHECKING:
    from .view import ExecutionGraphView


_SEQUENCES = {"pattern", "variant", "loop", "graph_path"}  # Columns holding sequences; other lists are sets.


def _cell(value: Any, column: str = "") -> str:
    if isinstance(value, list | tuple):
        value = (" → " if column in _SEQUENCES else ", ").join(str(item) for item in value)
    elif isinstance(value, dict):
        value = ", ".join(f"{key}: {item}" for key, item in value.items())
    elif isinstance(value, float):
        value = f"{value:.3g}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(rows: list[dict[str, Any]], columns: list[str], headers: list[str] | None = None) -> list[str]:
    if not rows:
        return ["_None found._", ""]
    lines = ["| " + " | ".join(headers or columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(row.get(column, ""), column) for column in columns) + " |" for row in rows]
    return [*lines, ""]


def markdown(view: ExecutionGraphView) -> str:
    """The report: overview, graph backbone, variants, loops, indicators, interventions, cost, entities, archetypes,
    drift and validation."""
    summary, mined = view.summary(), view.mined
    lines = [
        "# Abstract Execution Graph",
        "",
        f"{summary['cases']} cases from {summary['sessions']} sessions "
        f"({', '.join(f'{k} {v}' for k, v in summary['harnesses'].items())}); {summary['events']} events, "
        f"{summary['labels']} distinct labels, abstraction `{summary['abstraction']}` with {summary['states']} states "
        f"and {summary['edges']} transitions; {summary['tokens']:,} tokens"
        + (f", ${summary['cost']}" if summary["cost"] else "") + ".",
        "",
        "Outcomes: " + ", ".join(f"{k} {v}" for k, v in summary["outcomes"].items()) + ". Outcomes are heuristic; "
        "patterns are correlations to read, not causes.",
        "",
    ]  # fmt: skip
    lines += [f"> {note}" for note in summary["notes"]]
    happy = mined.happy_path
    taken = ", observed in a real case" if happy["graph_path_observed"] else ", never taken by any one case"
    lines += [
        "## Happy path",
        "",
        f"Most common completed variant ({happy['variant_cases']} cases): `{' → '.join(happy['variant'])}`",
        "",
        (
            f"Most probable path through the graph (P = {happy['graph_path_probability']}{taken}): "
            f"`{' → '.join(happy['graph_path'])}`"
        ),
        "",
    ]
    lines += ["## Top states", ""]
    nodes = [node.to_dict() for node in sorted(view.graph.nodes.values(), key=lambda n: -n.visits)
             if node.name != START and not node.name.startswith(END)][:15]  # fmt: skip
    for node in nodes:
        node["sample"] = node["samples"][0][:80] if node["samples"] else ""
    lines += _table(nodes, ["name", "visits", "sessions", "avg_repeat", "avg_tokens", "avg_dwell_seconds", "sample"])
    lines += ["## Variants", ""]
    lines += _table(mined.variants[:10], ["cases", "share", "cumulative", "variant"])
    lines += ["## Frequent subsequences (PrefixSpan, gaps allowed)", ""]
    lines += _table(mined.subsequences, ["cases", "share", "pattern"])
    loops_note = (
        "Exit probability is P(exit at iteration k | reached k); flat or falling after k ≥ 3 means stuck, rising "
        "means productive retries. Robust: also found under the validation abstraction."
    )
    lines += ["## Loops", "", loops_note, ""]
    lines += _table(mined.loops, ["loop", "kind", "cases", "instances", "median_iterations", "max_iterations",
                                  "in_failed_cases", "trend", "robust", "exit_probability", "examples"])  # fmt: skip
    lines += ["## Failure indicators (failed vs completed cases)", ""]
    columns = ["pattern", "group_cases", "other_cases", "group_share", "other_share", "log_odds", "z", "robust"]
    headers = ["pattern", "failed cases", "completed cases", "failed share", "completed share", "log-odds", "z",
               "robust"]  # fmt: skip
    lines += _table(mined.contrast["over"], columns, headers)
    lines += ["## Success indicators", ""]
    lines += _table(mined.contrast["under"], columns, headers)
    if mined.baseline is not None:
        lines += ["## Selection vs baseline", ""]
        baseline_headers = ["pattern", "selected cases", "baseline cases", "selected share", "baseline share",
                            "log-odds", "z"]  # fmt: skip
        lines += _table(mined.baseline["over"], columns[:-1], baseline_headers)
    lines += _failure_sections(view)
    lines += ["## Intervention points", "", "States right before the user corrects or stops the agent.", ""]
    lines += _table(mined.interventions, ["state", "intervention", "count", "rate", "examples"])
    sinks_note = (
        "Completion of cases passing through a state against the baseline; adjusted compares cases of similar length."
    )
    lines += ["## Failure sinks", "", sinks_note, ""]
    lines += _table(mined.sinks, ["state", "cases", "completed", "baseline", "delta", "adjusted_delta",
                                  "last_in_failed", "last_share"])  # fmt: skip
    lines += ["## Branching entropy", ""]
    lines += _table(mined.entropy, ["state", "visits", "successors", "entropy", "entropy_completed",
                                    "entropy_failed"])  # fmt: skip
    lines += ["## Cost hotspots", ""]
    lines += _table(mined.cost["states"][:10], ["state", "tokens", "share", "tokens_per_visit", "cost"])
    lines += _table(mined.cost["loops"][:10], ["loop", "tokens", "share"])
    entities = view.entities
    lines += ["## Entities", "", "### Files edited across sessions", ""]
    lines += _table(entities["edit_hotspots"], ["file", "sessions", "edits"])
    lines += ["### Files in failure loops", ""]
    lines += _table(entities["failure_loop_files"], ["file", "cases", "steps"])
    lines += ["### Models and tools in failure loops", ""]
    lines += _table(entities["model_tools"], ["model", "tool", "calls", "fail_rate", "in_failure_loops"])
    lines += ["### Projects", ""]
    lines += _table(entities["projects"], ["project", "cases", "completed"])
    found = view.archetypes
    lines += ["## Archetypes", "", f"Method: {found.get('method') or found.get('note')}.", ""]
    lines += _table(found.get("clusters", []), ["name", "cases", "completed", "outcomes", "median_states",
                                                "median_tokens", "distinguishing", "examples"])  # fmt: skip
    lines += ["## Drift", "", "### By month", ""]
    drift_columns = ["key", "cases", "completed", "median_states", "loop_cases", "tokens", "top_states"]
    lines += _table(mined.drift["month"], drift_columns)
    lines += ["### By model", ""]
    lines += _table(mined.drift["model"], drift_columns)
    if view.revisions is not None:
        modes = list(view.revisions[0]["modes"]) if view.revisions else []
        lines += ["### By agent revision", "", ("Share of cases with each of the most common failure modes, per "
                  "revision of the configuration the cases ran under: did a change move the failures it targeted?"), ""]
        rows = [{**row, **{f"mode:{m}": row["modes"].get(m, 0) for m in modes}} for row in view.revisions]
        lines += _table(rows, ["key", "from", "cases", "completed", *(f"mode:{m}" for m in modes)],
                        ["revision", "from", "cases", "completed", *modes])  # fmt: skip
    if view.validation:
        validation = view.validation
        checked = (
            f"Re-checked under `{validation['abstraction']}`: {validation['robust_loops']} loops and "
            f"{validation['robust_failure_indicators']} failure indicators hold up. Read the example cases of each "
            "loop and archetype, and spot-check the intent sample (`intents_sample.csv` in an export)."
        )
        lines += ["## Validation", "", checked, ""]
    return "\n".join(lines) + "\n"


def _failure_sections(view: ExecutionGraphView) -> list[str]:
    """Failure modes, critical steps, the judge's agreement with the rules, and the gold-set evaluation."""
    report = view.failures
    if report is None:
        return []
    note = ("Named failure modes (MAST, AgentRx) found by rules in the actions, and by the LLM judge when it ran. "
            "Compare the failed and completed shares: a mode common in completed cases too is a weak signal. "
            "Findings are hints with evidence; read the examples.")  # fmt: skip
    lines = ["## Failure modes", "", note, ""]
    lines += _table(report.modes, ["mode", "category", "source", "cases", "failed_cases", "completed_cases",
                                   "failed_share", "completed_share", "sources", "examples"])  # fmt: skip
    critical_note = ("Each failed case's critical step: the first failure of its final run of failures that is never "
                     "recovered from.")  # fmt: skip
    lines += ["## Critical steps", "", critical_note, ""]
    lines += _table(report.critical, ["category", "cases", "share", "outcomes", "labels", "states_before", "examples"])
    if view.judge is not None:
        judged = view.judge["agreement"]
        steps = judged["critical_step"]
        lines += ["## Judge", "", (
            f"{judged['cases']} window(s) judged by {view.judge['model']} ({view.judge['calls']} call(s)). Outcome "
            f"against the heuristic: {_cell(judged['outcome']) or 'none'}. Critical step compared on "
            f"{steps['compared']} case(s): exact {steps['exact']}, within 3 steps {steps['within_3']}."),
            ""]  # fmt: skip
        lines += _table(judged["modes"], ["mode", "both", "rules_only", "judge_only"])
    if view.gold is not None:
        gold = view.gold
        lines += ["## Gold set", "", f"{gold['labelled']} hand-labelled case(s).", ""]
        rows = [{"who": who, **{k: v for k, v in gold[who].items() if k != "modes"}} for who in ("rule", "judge")]
        lines += _table(rows, ["who", "cases", "step_exact", "step_within_1", "step_within_3", "category_accuracy"])
        modes = [{"who": who, "mode": mode, **values} for who in ("rule", "judge")
                 for mode, values in gold[who]["modes"].items()]  # fmt: skip
        lines += _table(modes, ["who", "mode", "predicted", "gold", "kappa"])
    return lines


def _id(name: str) -> str:
    return "s_" + re.sub(r"\W", "_", name)


def _label(name: str) -> str:
    return html.escape(name).replace('"', "#quot;")


def mermaid(view: ExecutionGraphView, *, edges: int = 40) -> str:
    """The state graph: the ``edges`` most frequent transitions, states coloured by the completion rate of the
    cases that visit them against all cases (green 5 points or more above, red 5 or more below), edges labelled
    with count and P(target | source); dashed edges close loops."""
    chosen = sorted(view.graph.edges.values(), key=lambda edge: -edge.count)[:edges]
    names = {edge.source for edge in chosen} | {edge.target for edge in chosen}
    outcome_of = {s.case.case_id: s.success for s in view.sequences}
    baseline = sum(outcome_of.values()) / len(outcome_of) if outcome_of else 0.0
    lines = ["flowchart LR"]
    for name in sorted(names):
        node = view.graph.nodes[name]
        rate = sum(outcome_of[case] for case in node.cases) / len(node.cases) if node.cases else 0
        # Relative to the baseline: what matters is how a state's cases fare against all cases.
        style = "good" if rate >= baseline + 0.05 else "bad" if rate <= baseline - 0.05 else "mid"
        if name == START or name.startswith(END):
            style = "terminal"
        lines.append(f'    {_id(name)}["{_label(name)}<br/>{node.visits} visits, {rate:.0%} completed"]:::{style}')
    for edge in chosen:
        arrow = "-.->" if edge.loop_back else "-->"
        lines.append(f'    {_id(edge.source)} {arrow}|"{edge.count} · {edge.probability:.0%}"| {_id(edge.target)}')
    lines += [
        "    classDef good fill:#d4edda,stroke:#2e7d32",
        "    classDef mid fill:#fff3cd,stroke:#b8860b",
        "    classDef bad fill:#f8d7da,stroke:#c62828",
        "    classDef terminal fill:#e8eaf6,stroke:#3949ab",
    ]
    return "\n".join(lines) + "\n"


def sankey(view: ExecutionGraphView, *, steps: int = 6) -> str:
    """A Mermaid Sankey of how cases move through phases in their first ``steps`` phases, then end."""
    flows: Counter[tuple[str, str]] = Counter()
    for sequence in view.sequences:
        phases: list[str] = []
        for state in sequence.states:
            name = coarsen(state.name)
            if not phases or phases[-1] != name:
                phases.append(name)
        path = [f"{i + 1}. {name}" for i, name in enumerate(phases[:steps])] + [f"{END} {sequence.outcome}"]
        flows.update(pairwise(path))
    lines = ["sankey-beta", ""]
    lines += [f"{source.replace(',', ' ')},{target.replace(',', ' ')},{count}"
              for (source, target), count in flows.most_common()]  # fmt: skip
    return "\n".join(lines) + "\n"


def _html(markdown_text: str) -> str:
    """A small Markdown subset (headings, tables, paragraphs, quotes, code spans) as HTML."""
    out: list[str] = []
    rows: list[str] = []

    def inline(text: str) -> str:
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text).replace("\\|", "|"))

    def flush() -> None:
        if rows:
            out.append("<table>" + "".join(rows) + "</table>")
            rows.clear()

    for line in markdown_text.splitlines():
        if line.startswith("|"):
            if re.fullmatch(r"\|(---\|)+", line):
                continue
            cells = re.split(r"(?<!\\)\|", line.strip()[1:-1])
            tag = "th" if not rows else "td"
            rows.append("<tr>" + "".join(f"<{tag}>{inline(c.strip())}</{tag}>" for c in cells) + "</tr>")
            continue
        flush()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{inline(line[level:].strip())}</h{level}>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)


def export(view: ExecutionGraphView, folder: Path) -> Path:
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    events = [event.to_dict() for case in view.cases for event in case.events]
    states = [{"case_id": s.case.case_id, "session_id": s.case.session_id, "index": i, "state": state.name,
               "repeat": state.repeat, "tokens": state.tokens, "start": state.start, "end": state.end,
               "outcome": s.outcome} for s in view.sequences for i, state in enumerate(s.states)]  # fmt: skip
    _table_file(folder / "events", events)
    _table_file(folder / "states", states)
    with (folder / "event_log.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)  # pm4py: case:concept:name, concept:name, time:timestamp.
        writer.writerow(["case:concept:name", "concept:name", "time:timestamp", "repeat", "outcome"])
        for row in states:
            writer.writerow([row["case_id"], row["state"], row["start"] or "", row["repeat"], row["outcome"]])
    with (folder / "intents_sample.csv").open("w", newline="", encoding="utf-8") as stream:
        intents = csv.DictWriter(stream, fieldnames=["case", "intent", "text"])
        intents.writeheader()
        intents.writerows(view.intents)
    (folder / "graph.json").write_text(json.dumps(view.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
                                       encoding="utf-8")  # fmt: skip
    if view.failures is not None:
        from .failures import write_gold_template

        rows = [finding.to_dict() for finding in view.failures.findings]
        (folder / "findings.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                               encoding="utf-8")  # fmt: skip
        write_gold_template(folder / "gold_set.csv", view.sequences, view.failures.findings,
                            view.failures.critical_steps, {v.case_id: v for v in view.verdicts})  # fmt: skip
    report = markdown(view)
    graph, flow = mermaid(view), sankey(view)
    (folder / "report.md").write_text(report, encoding="utf-8")
    (folder / "graph.mmd").write_text(graph, encoding="utf-8")
    (folder / "sankey.mmd").write_text(flow, encoding="utf-8")
    page = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Abstract Execution Graph</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#18212b;max-width:1400px}}
table{{border-collapse:collapse;margin:.5rem 0 1.5rem;font-size:13px}}td,th{{border:1px solid #d9e0e8;padding:3px 8px;
text-align:left;vertical-align:top}}th{{background:#f3f5f8}}code{{background:#f3f5f8;padding:0 3px;border-radius:3px}}
.graph{{overflow:auto;border:1px solid #d9e0e8;padding:1rem;border-radius:8px;background:#fff}}</style>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{startOnLoad:true,securityLevel:"strict",theme:"neutral",maxEdges:2000}});</script>
<h1>Abstract Execution Graph</h1>
<h2>State graph</h2><p>Green: cases visiting the state complete more often than average; red: less often (5 points
or more either way). Dashed edges close loops. Edge labels: count · P(target | source).</p>
<div class="graph"><pre class="mermaid">{html.escape(graph, quote=False)}</pre></div>
<h2>Phases (Sankey)</h2><div class="graph"><pre class="mermaid">{html.escape(flow, quote=False)}</pre></div>
{_html(report.split(chr(10), 2)[2])}
<p>Files: <a href="report.md">report.md</a> · <a href="graph.json">graph.json</a> · events and states tables ·
<a href="event_log.csv">event_log.csv</a> (pm4py) · <a href="intents_sample.csv">intents_sample.csv</a> ·
<a href="findings.jsonl">findings.jsonl</a> · <a href="gold_set.csv">gold_set.csv</a> (to hand-label)</p></html>
"""
    (folder / "index.html").write_text(page, encoding="utf-8")
    return folder


def _table_file(stem: Path, rows: list[dict[str, Any]]) -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        path = stem.with_suffix(".parquet")
        pq.write_table(pa.Table.from_pylist(rows), path)
        return path
    except ImportError:
        path = stem.with_suffix(".jsonl")
        path.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                        encoding="utf-8")  # fmt: skip
        return path
