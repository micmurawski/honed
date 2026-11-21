"""The whole pipeline over a set of threads, and step 7: validation of what it found.

Validation re-runs the case sequences under a second abstraction (by default ``phase`` when the main one is finer,
``phase+outcome`` otherwise) and marks loops and failure indicators that hold up under both as robust. It also draws
samples to read by hand: example cases per loop and archetype, and a seeded sample of user-intent labels.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...redact import Redaction
from .archetypes import archetypes as find_archetypes
from .entities import entity_report
from .events import Case, cases_from_threads
from .failures import FailureReport, drift_by, evaluate
from .graph import FAILURES, CaseSequence, StateGraph, aggregate, outcome
from .judge import FailureJudge, Verdict, agreement, choose
from .labels import LLMLabeler, is_meta, label_case
from .mining import Mined, contrast, loop_instances, mine
from .states import abstract, check_abstraction, coarsen


def sequences_of(cases: Sequence[Case], abstraction: str, labeler: LLMLabeler | None = None) -> list[CaseSequence]:
    """Label every case (with the model for free text when ``labeler`` is given) and abstract it."""
    user_labels: dict[str, str] = {}
    assistant_labels: dict[str, str] = {}
    if labeler is not None:
        users = [e.text.strip() for c in cases for e in c.events
                 if e.kind == "user_msg" and e.text.strip() and not is_meta(e.text)]  # fmt: skip
        replies = [e.text.strip() for c in cases for e in c.events if e.kind == "assistant_text" and e.text.strip()]
        user_labels = labeler.label("user", users)
        assistant_labels = labeler.label("assistant", replies)
    sequences = []
    for case in cases:
        actions = label_case(case, user_labels=user_labels, assistant_labels=assistant_labels)
        sequences.append(CaseSequence(case, actions, abstract(actions, abstraction), outcome(case, actions)))
    return sequences


def _phase_cycle(cycle: Iterable[str]) -> tuple[str, ...]:
    names: list[str] = []
    for name in (coarsen(item) for item in cycle):
        if not names or names[-1] != name:
            names.append(name)
    if len(names) > 1 and names[0] == names[-1]:
        names.pop()
    return tuple(min(tuple(names[i:] + names[:i]) for i in range(len(names)))) if names else ()


def validate(main: Mined, sequences: Sequence[CaseSequence], other: Sequence[CaseSequence],
             abstraction: str) -> dict[str, Any]:  # fmt: skip
    """Mark loops and failure indicators that also appear under the second abstraction."""
    instances = [instance for sequence in other for instance in loop_instances(sequence)]
    coarse_loops = {_phase_cycle(instance.cycle) for instance in instances}
    for row in main.loops:
        row["robust"] = _phase_cycle(row["loop"]) in coarse_loops
    failed = [s for s in other if s.outcome in FAILURES]
    completed = [s for s in other if s.success]
    table = contrast(failed, completed, top=10**6)
    signs = {tuple(coarsen(n) for n in row["pattern"]): 1 for row in table["over"] if row["log_odds"] > 0.3}
    signs |= {tuple(coarsen(n) for n in row["pattern"]): -1 for row in table["under"] if row["log_odds"] < -0.3}
    for side, sign in (("over", 1), ("under", -1)):
        for row in main.contrast[side]:
            pattern = tuple(dict.fromkeys(coarsen(n) for n in row["pattern"]))
            row["robust"] = signs.get(pattern) == sign or signs.get(tuple(coarsen(n) for n in row["pattern"])) == sign
    robust_loops = sum(row["robust"] for row in main.loops)
    robust_indicators = sum(row["robust"] for row in main.contrast["over"])
    return {"abstraction": abstraction, "robust_loops": f"{robust_loops}/{len(main.loops)}",
            "robust_failure_indicators": f"{robust_indicators}/{len(main.contrast['over'])}",
            "cases": len(other)}  # fmt: skip


def intent_sample(sequences: Sequence[CaseSequence], *, size: int = 50, seed: int = 0) -> list[dict[str, str]]:
    """User messages with their intent labels, to check about fifty of them by hand."""
    rows = [{"case": s.case.case_id, "intent": a.label.split(":", 1)[1], "text": a.sample}
            for s in sequences for a in s.actions if a.family == "USER"]  # fmt: skip
    random.Random(seed).shuffle(rows)
    return rows[:size]


@dataclass
class ExecutionGraphView:
    """Cases, their sequences, the aggregated state graph, and everything mined from it."""

    abstraction: str
    cases: list[Case]
    sequences: list[CaseSequence]
    graph: StateGraph
    mined: Mined
    entities: dict[str, list[dict[str, Any]]]
    archetypes: dict[str, Any]
    validation: dict[str, Any] | None
    intents: list[dict[str, str]]
    failures: FailureReport | None = None
    verdicts: list[Verdict] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    revisions: list[dict[str, Any]] | None = None
    gold: dict[str, Any] | None = None
    labeler: dict[str, Any] | None = None
    baseline_cases: int = 0
    notes: list[str] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        threads: Iterable[Any],
        *,
        abstraction: str = "phase+outcome",
        compare: str | None = "auto",
        baseline: Iterable[Any] | None = None,
        labeler: LLMLabeler | None = None,
        name_clusters: bool = False,
        redaction: Redaction | None = None,
        min_support: float = 0.03,
        top: int = 15,
        judge: FailureJudge | None = None,
        judge_limit: int = 20,
        revision_of: Callable[[Case], str | None] | None = None,
        gold: str | Path | None = None,
    ) -> ExecutionGraphView:
        """Run the pipeline over ``threads`` (``Thread`` objects); ``baseline`` threads are contrasted against them.

        ``compare`` is the abstraction validation re-checks patterns under (``auto``: ``phase``, or ``phase+outcome``
        when the main abstraction is ``phase``; None skips validation). ``judge`` judges up to ``judge_limit``
        windows around critical steps; ``revision_of`` names the agent revision each case ran under, for a drift table
        by revision; ``gold`` is a hand-labelled gold-set CSV to score rules and judge against.
        """
        check_abstraction(abstraction)
        redaction = Redaction() if redaction is None else redaction
        cases = list(cases_from_threads(threads, redaction=redaction))
        sequences = sequences_of(cases, abstraction, labeler)
        baseline_sequences = None
        if baseline is not None:
            baseline_sequences = sequences_of(list(cases_from_threads(baseline, redaction=redaction)), abstraction,
                                              labeler)  # fmt: skip
        graph = aggregate(sequences)
        mined = mine(sequences, graph, baseline=baseline_sequences, min_support=min_support, top=top)
        entities = entity_report(sequences, mined.instances, top=top)
        namer = labeler.complete if (labeler is not None and name_clusters) else None
        clusters = find_archetypes(sequences, name=namer)
        validation = None
        if compare is not None and sequences:
            other = ("phase+outcome" if abstraction == "phase" else "phase") if compare == "auto" else compare
            check_abstraction(other)
            validation = validate(mined, sequences, sequences_of(cases, other, labeler), other)
        failures = FailureReport.build(sequences)
        verdicts: list[Verdict] = []
        judged = None
        if judge is not None:
            rules = failures.by_case()
            verdicts = judge.judge_all(choose(sequences, rules, limit=judge_limit))
            failures = FailureReport.build(sequences, [f for v in verdicts for f in v.findings])
            judged = {"calls": judge.calls, "model": judge.model, "failures": len(judge.failures),
                      "agreement": agreement(verdicts, rules, sequences)}  # fmt: skip
        revisions = None
        if revision_of is not None:
            revisions = drift_by(sequences, failures.findings, lambda s: revision_of(s.case))
        notes = []
        if judge is not None and judge.failures:
            notes.append(f"{len(judge.failures)} judge call(s) gave no acceptable answer")
        if labeler is not None and labeler.failures:
            notes.append(f"{len(labeler.failures)} LLM labelling call(s) failed; rules labelled those texts")
        return cls(
            abstraction, cases, sequences, graph, mined, entities, clusters, validation, intent_sample(sequences),
            failures=failures, verdicts=verdicts, judge=judged, revisions=revisions,
            gold=evaluate(Path(gold)) if gold is not None else None,
            labeler={"calls": labeler.calls, "cached": len(labeler.cache)} if labeler is not None else None,
            baseline_cases=len(baseline_sequences or []), notes=notes,
        )  # fmt: skip

    def summary(self) -> dict[str, Any]:
        labels = Counter(action.label for s in self.sequences for action in s.actions)
        return {
            "abstraction": self.abstraction,
            "cases": len(self.cases),
            "sessions": len({case.session_id for case in self.cases}),
            "harnesses": dict(Counter(case.harness for case in self.cases)),
            "outcomes": dict(Counter(s.outcome for s in self.sequences).most_common()),
            "events": sum(len(case.events) for case in self.cases),
            "labels": len(labels),
            "states": len(self.graph.nodes),
            "edges": len(self.graph.edges),
            "tokens": sum(case.tokens for case in self.cases),
            "cost": round(sum(case.cost for case in self.cases), 4),
            "abandoned_branches": sum(case.abandoned_branch for case in self.cases),
            "baseline_cases": self.baseline_cases,
            "failure_findings": len(self.failures.findings) if self.failures else 0,
            "critical_steps": len(self.failures.critical_steps) if self.failures else 0,
            "llm_labeler": self.labeler,
            "notes": self.notes,
        }

    def to_dict(self) -> dict[str, Any]:
        mined = self.mined
        return {
            "summary": self.summary(),
            "graph": self.graph.to_dict(),
            "variants": mined.variants,
            "frequent_subsequences": mined.subsequences,
            "loops": mined.loops,
            "failure_indicators": mined.contrast["over"],
            "success_indicators": mined.contrast["under"],
            "baseline_contrast": mined.baseline,
            "interventions": mined.interventions,
            "cost_hotspots": mined.cost,
            "drift": mined.drift,
            "happy_path": mined.happy_path,
            "branching_entropy": mined.entropy,
            "failure_sinks": mined.sinks,
            "entities": self.entities,
            "archetypes": self.archetypes,
            "validation": self.validation,
            "intent_sample": self.intents,
            "failures": self.failures.to_dict() if self.failures else None,
            "judge": {**self.judge, "verdicts": [v.to_dict() for v in self.verdicts]} if self.judge else None,
            "drift_by_revision": self.revisions,
            "gold_evaluation": self.gold,
        }

    def findings_of(self, session_id: str) -> tuple[list[Any], Any]:
        """The findings and the critical step of a session's cases (its root case first), e.g. to focus a reflector."""
        if self.failures is None:
            return [], None
        cases = [s.case for s in self.sequences if s.case.session_id == session_id]
        cases.sort(key=lambda case: not case.root)
        order = {case.case_id: i for i, case in enumerate(cases)}
        findings = sorted((f for f in self.failures.findings if f.case_id in order),
                          key=lambda f: (order[f.case_id], f.action_index))  # fmt: skip
        steps = sorted((step for step in self.failures.critical_steps if step.case_id in order),
                       key=lambda step: order[step.case_id])  # fmt: skip
        return findings, steps[0] if steps else None

    def to_markdown(self) -> str:
        from .report import markdown

        return markdown(self)

    def to_mermaid(self, *, edges: int = 40) -> str:
        from .report import mermaid

        return mermaid(self, edges=edges)

    def to_sankey(self, *, steps: int = 6) -> str:
        from .report import sankey

        return sankey(self, steps=steps)


def export_execution_graph(view: ExecutionGraphView, folder: str | Path) -> Path:
    """Write the tables, graph, report and an HTML page to ``folder``."""
    from .report import export

    return export(view, Path(folder))


def session_failures(histories: Iterable[Any], *,
                     redaction: Redaction | None = None) -> dict[str, tuple[list[Any], Any]]:  # fmt: skip
    """The rule-based findings and the critical step (of the root case first) of each history (a ``Session``, all
    its threads, or a ``Thread``), by session ID: cheap, no model calls. Feed ``failures.focus`` to a reflector."""
    threads = [thread for history in histories for thread in getattr(history, "threads", [history])]
    cases = list(cases_from_threads(threads, redaction=Redaction() if redaction is None else redaction))
    sequences = sequences_of(cases, "phase+outcome")
    report = FailureReport.build(sequences)
    found: dict[str, tuple[list[Any], Any]] = {}
    for session_id in dict.fromkeys(case.session_id for case in cases):
        ids = [s.case.case_id for s in sorted((s for s in sequences if s.case.session_id == session_id),
                                              key=lambda s: not s.case.root)]  # fmt: skip
        order = {case_id: i for i, case_id in enumerate(ids)}
        findings = sorted((f for f in report.findings if f.case_id in order),
                          key=lambda f: (order[f.case_id], f.action_index))  # fmt: skip
        steps = sorted((st for st in report.critical_steps if st.case_id in order), key=lambda st: order[st.case_id])
        found[session_id] = (findings, steps[0] if steps else None)
    return found
