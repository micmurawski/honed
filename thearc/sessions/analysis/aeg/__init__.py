"""Abstract Execution Graph (AEG): mine how agent sessions flow, loop, fail and get corrected.

The pipeline follows ``AEG.md``:

0. ``events``   — a canonical event table per case (a session's thread, one branch of it), with outcomes, usage,
                  model, files and commands; tree-shaped sessions are linearised into one case per branch.
1. ``labels``   — every event mapped to a small action vocabulary by rules (an LLM can label free text instead).
2. ``states``   — labels abstracted to phases (+ outcome, or a k-tail), repeats collapsed with their counts.
3. ``graph``    — per-case sequences START → … → END:<outcome>, aggregated into one state graph.
4. ``entities`` — files, tools, models and projects linked to events: hotspots and where failure loops happen.
5. ``mining``   — variants, frequent subsequences, loops, outcome contrast, interventions, cost, drift, archetypes,
                  and graph metrics (happy path, branching entropy, loop exit, failure sinks).
6. ``report``   — Markdown, JSON, Mermaid (state graph and Sankey), and an exportable bundle with an HTML report.
7. validation   — patterns re-checked under a second abstraction; samples to read by hand.
8. ``failures`` — named failure modes (MAST, AgentRx) by rules, each failed case's critical step, a gold set to score
                  them; ``judge`` — an optional LLM judge of the windows around critical steps.

Start from ``ExecutionGraphView.build(threads)``.
"""

from .failures import TAXONOMY, CriticalStep, Finding, failure_mode_scale, focus
from .judge import FailureJudge
from .view import ExecutionGraphView, export_execution_graph, session_failures

__all__ = [
    "TAXONOMY",
    "CriticalStep",
    "ExecutionGraphView",
    "FailureJudge",
    "Finding",
    "export_execution_graph",
    "failure_mode_scale",
    "focus",
    "session_failures",
]
