"""Named failures: one taxonomy of failure modes, rule-based detectors that find them in cases, and each failed
case's critical step.

The taxonomy merges MAST (Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*, 2025: 14 modes in three categories)
with AgentRx (critical-step localisation and its failure categories) for single coding agents. One table serves the
report, the LLM judge (``judge.py``) and the rating scale reflections use (``failure_mode_scale``), so the three never
drift apart.

Detectors read a case's actions, not its states, so what they find does not depend on the abstraction. A detector
fires only on explicit evidence: an unclear case yields no finding. Findings are hints with evidence, to be read; the
judge and a hand-labelled gold set (``evaluate``) measure how far they can be trusted.

The critical step of a failed case is the first failure of its final run of failures that is never recovered from (a
later step of the same kind that does not fail, or the user approving, recovers a failure). A case that fails with no
failing step has its critical step at the user's intervention, or at its last step (``no_final_answer``).
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .graph import FAILURES, SUCCESS, CaseSequence
from .labels import Action

if TYPE_CHECKING:
    from thearc.resources.ratings import CategoricalScale

    from .events import CaseEvent


@dataclass(frozen=True)
class Mode:
    """A failure mode of the taxonomy."""

    id: str
    category: str  # specification, execution, verification, tooling, system
    source: str  # Where it comes from: MAST 3.1, AgentRx, …
    definition: str
    detected_by: str  # rules, judge, or rules+judge


TAXONOMY: tuple[Mode, ...] = (
    # Specification and planning (MAST category 1).
    Mode("disobey_task_spec", "specification", "MAST 1.1",
         "The agent ignored a stated constraint or requirement of the task.", "judge"),
    Mode("step_repetition", "specification", "MAST 1.3",
         "The same call with the same arguments and the same outcome, three or more times, with no edit between.",
         "rules+judge"),
    Mode("history_loss", "specification", "MAST 1.4",
         "After a context compaction the agent re-read what it had already read, having lost it.", "rules+judge"),
    Mode("unaware_of_termination", "specification", "MAST 1.5",
         "The agent did not recognise when the task was finished, or stopped before it was.", "judge"),
    # Execution and alignment (MAST category 2, AgentRx).
    Mode("failed_to_clarify", "execution", "MAST 2.2",
         "A short, ambiguous request acted on without exploring or asking, then corrected by the user.", "rules+judge"),
    Mode("task_derailment", "execution", "MAST 2.3",
         "The agent drifted from the task to other work.", "judge"),
    Mode("ignored_input", "execution", "MAST 2.4",
         "The agent ignored what the user or a tool result told it.", "judge"),
    Mode("delegation_failed", "execution", "MAST 2.5",
         "A subagent failed or was abandoned while the parent went on to claim success.", "rules+judge"),
    Mode("reasoning_action_mismatch", "execution", "MAST 2.6",
         "What the agent said it would do differs from what it did.", "judge"),
    Mode("plan_adherence", "execution", "AgentRx",
         "The agent departed from its own plan, or the user's, without saying why.", "judge"),
    Mode("invented_information", "execution", "AgentRx",
         "The agent stated facts (paths, APIs, results) that nothing in the session supports.", "judge"),
    # Verification and termination (MAST category 3).
    Mode("premature_done", "verification", "MAST 3.1",
         "The agent finished while the last check after its last edit was failing.", "rules+judge"),
    Mode("claim_contradicted", "verification", "AgentRx",
         "The final answer claims success that the last check contradicts.", "rules+judge"),
    Mode("no_verification", "verification", "MAST 3.2",
         "The agent edited code and finished without running any check after its last edit.", "rules+judge"),
    Mode("incorrect_verification", "verification", "MAST 3.3",
         "A check passed, yet the user corrected the result before any further edit.", "rules+judge"),
    # Tools and environment (AgentRx).
    Mode("invalid_invocation", "tooling", "AgentRx",
         "A tool was called with invalid arguments or does not exist.", "rules+judge"),
    Mode("guardrail_block", "tooling", "AgentRx",
         "A permission, approval or sandbox rule blocked the step.", "rules+judge"),
    Mode("system_failure", "system", "AgentRx",
         "The model API or the environment failed: errors, timeouts, rate limits.", "rules+judge"),
)  # fmt: skip
MODES = {mode.id: mode for mode in TAXONOMY}
CRITICAL_CATEGORIES = ("check_failed", "edit_failed", "exploration_failed", "tool_failed", "invalid_invocation",
                       "guardrail", "system_failure", "user_intervention", "no_final_answer")  # fmt: skip


def failure_mode_scale(name: str = "failure_mode") -> CategoricalScale:
    """The taxonomy as an unordered rating scale: which failure mode cited configuration lines contributed to."""
    from thearc.resources.ratings import CategoricalScale

    return CategoricalScale(
        name,
        {mode.id: mode.definition for mode in TAXONOMY},
        description="The failure mode the cited lines contributed to (or failed to prevent), when the session shows "
        "one; leave it empty when no failure mode is evidenced.",
    )


@dataclass
class Finding:
    """A failure mode found in a case, at an action, with the evidence for it."""

    mode: str
    case_id: str
    action_index: int
    event_ids: list[str]
    evidence: str
    source: str = "rules"  # rules or judge
    confidence: str = "high"  # high, medium, low
    at: str | None = None  # When the action happened.

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "category": MODES[self.mode].category}


@dataclass
class CriticalStep:
    """Where a failed case went wrong for good."""

    case_id: str
    outcome: str
    action_index: int
    label: str
    category: str
    state_before: str
    sample: str
    event_ids: list[str] = field(default_factory=list)
    at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- detectors ---------------------------------------------------------------------------------------------------

_QUIET = {"THINK", "MODEL", "EXPLAIN"}
_CHECKS = {"TEST", "BUILD", "RUN"}
_SUCCESS_CLAIM = re.compile(
    r"(all (\d+ )?tests? (now )?pass|tests? (are |now )?(all )?passing|(now )?pass(es)? (all|now)|"
    r"\bfixed\b|\bresolved\b|works now|is now working|successfully|everything (works|passes)|✅|"
    r"build (is |now )?(green|passing|succeeds))",
    re.IGNORECASE,
)
_INVALID = re.compile(r"(<tool_use_error>|no such tool|inputvalidationerror|invalid (arguments?|parameters?|input)|"
                      r"unknown (tool|parameter|argument)|missing required (parameter|argument)|"
                      r"unexpected (keyword )?argument|is not a valid)", re.IGNORECASE)  # fmt: skip
_GUARDRAIL = re.compile(r"(requires? (approval|permission)|permission denied|operation not permitted|not permitted|"
                        r"sandbox|blocked by|denied by|user (rejected|denied)|doesn'?t want to proceed|"
                        r"approval (was )?(denied|rejected)|not allowed)", re.IGNORECASE)  # fmt: skip
_SYSTEM = re.compile(r"(timed? ?out|rate.?limit|overloaded|econnreset|connection (reset|refused)|\b50[234]\b|"
                     r"internal server error|service unavailable|api error)", re.IGNORECASE)  # fmt: skip


def _events(sequence: CaseSequence) -> dict[str, CaseEvent]:
    return {event.event_id: event for event in sequence.case.events}


def _result_text(action: Action, events: dict[str, CaseEvent]) -> str:
    """The text of the action's tool result (its first 500 characters and error lines)."""
    for event_id in action.event_ids:
        event = events.get(event_id)
        if event is not None and event.kind == "tool_result":
            return f"{event.error_text}\n{event.text}"
    return ""


def _full_text(action: Action, events: dict[str, CaseEvent]) -> str:
    event = events.get(action.event_ids[0]) if action.event_ids else None
    return (event.text if event is not None else "") or action.sample


def _base(action: Action) -> str:
    return action.sample.split(" ⟶ ", 1)[0].strip()


def _finding(mode: str, sequence: CaseSequence, index: int, evidence: str, *, involved: Iterable[int] = (),
             confidence: str = "high") -> Finding:  # fmt: skip
    actions = sequence.actions
    ids = [event_id for i in dict.fromkeys([*involved, index]) for event_id in actions[i].event_ids]
    return Finding(mode, sequence.case.case_id, index, ids, evidence[:300], confidence=confidence,
                   at=actions[index].start)


def _final(actions: Sequence[Action]) -> int | None:
    """The index of the case's final summary, when the case ends on one (bookkeeping steps aside)."""
    for index in range(len(actions) - 1, -1, -1):
        if actions[index].family in _QUIET or actions[index].family == "COMPACT":
            continue
        return index if actions[index].family == "FINAL_SUMMARY" else None
    return None


def _last_edit(actions: Sequence[Action], before: int) -> int | None:
    edits = [i for i in range(before) if actions[i].family == "EDIT" and not actions[i].label.startswith("EDIT:docs")]
    return edits[-1] if edits else None


def _blocked(action: Action, events: dict[str, CaseEvent]) -> bool:
    """A step that failed without running: blocked by a guardrail, called wrongly, or cut off by the system."""
    if action.outcome != "fail":
        return False
    text = _result_text(action, events)
    return any(pattern.search(text) for pattern in (_INVALID, _GUARDRAIL, _SYSTEM))


def _verification(sequence: CaseSequence, events: dict[str, CaseEvent]) -> list[Finding]:
    actions = sequence.actions
    found: list[Finding] = []
    final = _final(actions)
    if final is not None and (edit := _last_edit(actions, final)) is not None:
        # A check that never ran (blocked, invalid, cut off) says nothing about the code.
        checks = [i for i in range(edit + 1, final)
                  if actions[i].family in _CHECKS and not _blocked(actions[i], events)]
        judged = [i for i in checks if actions[i].outcome]
        if not checks:
            found.append(_finding("no_verification", sequence, final,
                                  f"last edit at step {edit} ({_base(actions[edit])[:80]}); no test, build or run "
                                  "before the final answer", involved=[edit]))  # fmt: skip
        elif judged and actions[judged[-1]].outcome == "fail":
            check = judged[-1]
            claim = _SUCCESS_CLAIM.search(_full_text(actions[final], events))
            text = f"{actions[check].label} at step {check} ({actions[check].sample[:100]})"
            if claim:
                found.append(_finding("claim_contradicted", sequence, final,
                                      f"{text}; the final answer says {claim.group(0)!r}", involved=[check]))
            found.append(_finding("premature_done", sequence, final, f"{text}; then the final answer",
                                  involved=[check]))  # fmt: skip
    for index, action in enumerate(actions):
        if action.family in {"TEST", "BUILD"} and action.outcome == "ok":
            for later in range(index + 1, len(actions)):
                follower = actions[later]
                if follower.family == "EDIT" or follower.family in _CHECKS:
                    break
                if follower.label == "USER:CORRECTION":
                    found.append(_finding("incorrect_verification", sequence, later,
                                          f"{action.label} at step {index} ({_base(action)[:80]}); then the user "
                                          f"corrected: {follower.sample[:100]!r}", involved=[index],
                                          confidence="medium"))  # fmt: skip
                    break
    return found


def _call(action: Action, events: dict[str, CaseEvent]) -> str:
    """What exactly was called: the whole command or the arguments' summary, not the sample's first line."""
    event = events.get(action.event_ids[0]) if action.event_ids else None
    if event is None or event.kind != "tool_call":
        return _base(action)
    return event.command or f"{event.tool_name} {event.args_summary}"


def _repetition(sequence: CaseSequence, events: dict[str, CaseEvent]) -> list[Finding]:
    actions = sequence.actions
    runs: list[tuple[tuple[str, str, str | None], list[int]]] = []
    current: dict[tuple[str, str, str | None], list[int]] = {}
    for index, action in enumerate(actions):
        if action.family == "EDIT" and action.outcome != "fail":
            current = {}  # An edit changes what a repeated call means.
            continue
        if action.tool is None or not _base(action) or (action.family == "EXPLORE" and action.outcome != "fail"):
            continue  # Reading a file again (often another part of it) is not being stuck.
        key = (action.label, _call(action, events), action.outcome)
        steps = current.setdefault(key, [])
        steps.append(index)
        if len(steps) == 3:
            runs.append((key, steps))  # The list goes on growing with the run.
    return [_finding("step_repetition", sequence, steps[0],
                     f"{label} {base[:100]!r} {len(steps)}× (steps {', '.join(map(str, steps[:6]))}"
                     f"{', …' if len(steps) > 6 else ''}) with no edit between", involved=steps)
            for (label, base, _), steps in runs]  # fmt: skip


def _clarification(sequence: CaseSequence, events: dict[str, CaseEvent]) -> list[Finding]:
    actions = sequence.actions
    first = next((i for i, a in enumerate(actions) if a.label == "USER:TASK_NEW"), None)
    if first is None or len(_full_text(actions[first], events).split()) >= 15:
        return []
    edit = next((i for i in range(first + 1, len(actions)) if actions[i].family in {"EDIT", "USER"}), None)
    if edit is None or actions[edit].family != "EDIT":
        return []
    if any(actions[i].family in {"EXPLORE", "ASK_USER", "WEB"} for i in range(first + 1, edit)):
        return []
    correction = next((i for i in range(edit + 1, len(actions)) if actions[i].family == "USER"), None)
    if correction is None or actions[correction].label != "USER:CORRECTION":
        return []
    return [_finding("failed_to_clarify", sequence, edit,
                     f"short request {actions[first].sample[:80]!r} edited on without exploring or asking (step "
                     f"{edit}); the user then corrected: {actions[correction].sample[:80]!r}",
                     involved=[first, correction], confidence="low")]  # fmt: skip


def _history_loss(sequence: CaseSequence) -> list[Finding]:
    actions = sequence.actions
    found = []
    for index, action in enumerate(actions):
        if action.family != "COMPACT":
            continue
        read = {path for a in actions[:index] if a.family == "EXPLORE" for path in a.paths}
        again = [i for i in range(index + 1, min(len(actions), index + 11))
                 if actions[i].family == "EXPLORE" and set(actions[i].paths) & read]  # fmt: skip
        files = {path for i in again for path in actions[i].paths if path in read}
        if len(files) >= 2:
            found.append(_finding("history_loss", sequence, again[0],
                                  f"after the compaction at step {index}, re-read {len(files)} files read before it: "
                                  f"{', '.join(sorted(files)[:3])}", involved=[index, *again],
                                  confidence="low"))  # fmt: skip
    return found


def _tooling(sequence: CaseSequence, events: dict[str, CaseEvent]) -> list[Finding]:
    found = []
    for index, action in enumerate(sequence.actions):
        if action.family == "API_ERROR":
            found.append(_finding("system_failure", sequence, index, f"API error: {action.sample[:120]!r}"))
            continue
        if action.outcome != "fail":
            continue
        text = _result_text(action, events)
        for mode, pattern in (("invalid_invocation", _INVALID), ("guardrail_block", _GUARDRAIL),
                              ("system_failure", _SYSTEM)):  # fmt: skip
            if match := pattern.search(text):
                line = next((line for line in text.splitlines() if match.group(0) in line), match.group(0))
                found.append(_finding(mode, sequence, index, f"{action.label} {_base(action)[:60]!r}: "
                                      f"{line.strip()[:160]!r}"))  # fmt: skip
                break
    return found


def _delegation(sequences: Sequence[CaseSequence]) -> list[Finding]:
    children: defaultdict[str, list[CaseSequence]] = defaultdict(list)
    for sequence in sequences:
        if sequence.case.parent_thread_id:
            children[sequence.case.parent_thread_id].append(sequence)
    found = []
    for parent in sequences:
        failed = [child for child in children.get(parent.case.thread_id, ()) if child.outcome in FAILURES]
        final = _final(parent.actions)
        if not failed or not parent.success or final is None:
            continue
        delegate = next((i for i, a in enumerate(parent.actions) if a.family == "DELEGATE"), final)
        names = ", ".join(f"{child.case.case_id} ({child.outcome})" for child in failed[:3])
        found.append(_finding("delegation_failed", parent, final,
                              f"subagent case(s) {names} failed, and the parent finished as completed",
                              involved=[delegate], confidence="medium"))  # fmt: skip
    return found


def detect(sequences: Sequence[CaseSequence]) -> list[Finding]:
    """Every rule-based finding in ``sequences``, in case order."""
    found: list[Finding] = []
    for sequence in sequences:
        events = _events(sequence)
        found += _verification(sequence, events)
        found += _repetition(sequence, events)
        found += _clarification(sequence, events)
        found += _history_loss(sequence)
        found += _tooling(sequence, events)
    found += _delegation(sequences)
    order = {sequence.case.case_id: i for i, sequence in enumerate(sequences)}
    return sorted(found, key=lambda f: (order[f.case_id], f.action_index, f.mode))


# --- critical step -----------------------------------------------------------------------------------------------


def _failed(action: Action) -> bool:
    return action.outcome == "fail" or action.family == "API_ERROR"


def _recovered(actions: Sequence[Action], index: int) -> bool:
    family = actions[index].family
    for later in actions[index + 1 :]:
        if later.label == "USER:APPROVE":
            return True
        if later.family == family and not _failed(later) and family != "API_ERROR":
            return True
        if family == "API_ERROR" and later.family not in _QUIET | {"API_ERROR", "USER"}:
            return True
    return False


def _category(action: Action, findings: Sequence[Finding], index: int) -> str:
    here = {finding.mode for finding in findings if finding.action_index == index}
    if action.family == "API_ERROR" or "system_failure" in here:
        return "system_failure"
    if "invalid_invocation" in here:
        return "invalid_invocation"
    if "guardrail_block" in here:
        return "guardrail"
    if action.family in _CHECKS:
        return "check_failed"
    if action.family == "EDIT":
        return "edit_failed"
    if action.family == "EXPLORE":
        return "exploration_failed"
    return "tool_failed"


def _state_before(sequence: CaseSequence, index: int) -> str:
    """The abstract state the case was in before the one holding action ``index``."""
    position = {id(action): i for i, action in enumerate(sequence.actions)}
    previous = "START"
    for state in sequence.states:
        indices = [position[id(action)] for action in state.actions if id(action) in position]
        if indices and index in indices:
            return previous
        if indices and min(indices) > index:
            return previous
        previous = state.name
    return previous


def critical_step(sequence: CaseSequence, findings: Sequence[Finding] = ()) -> CriticalStep | None:
    """The critical step of a failed case (None for a completed one)."""
    if sequence.outcome not in FAILURES or not sequence.actions:
        return None
    actions = sequence.actions
    unrecovered = [i for i, action in enumerate(actions) if _failed(action) and not _recovered(actions, i)]
    if unrecovered:
        start = unrecovered[-1]
        for previous in reversed(unrecovered[:-1]):  # Walk back through the final run of failures.
            between = actions[previous + 1 : start]
            if any(a.family in _CHECKS | {"EDIT"} and not _failed(a) for a in between):
                break
            start = previous
        index, category = start, _category(actions[start], findings, start)
    else:
        intervention = next((i for i in range(len(actions) - 1, -1, -1)
                             if actions[i].label in {"USER:CORRECTION", "USER:ABORT"}), None)  # fmt: skip
        if sequence.outcome == "user_aborted" and intervention is not None:
            index = next((i for i in range(intervention - 1, -1, -1) if actions[i].family not in _QUIET), intervention)
            category = "user_intervention"
        else:
            index = next((i for i in range(len(actions) - 1, -1, -1) if actions[i].family not in _QUIET),
                         len(actions) - 1)  # fmt: skip
            category = "no_final_answer"
    action = actions[index]
    return CriticalStep(sequence.case.case_id, sequence.outcome, index, action.label, category,
                        _state_before(sequence, index), action.sample[:160], list(action.event_ids),
                        action.start)  # fmt: skip


# --- tables ------------------------------------------------------------------------------------------------------


def mode_table(findings: Sequence[Finding], sequences: Sequence[CaseSequence], *,
               examples: int = 3) -> list[dict[str, Any]]:  # fmt: skip
    """Per failure mode: in how many failed and completed cases it was found, and examples."""
    outcome = {sequence.case.case_id: sequence.outcome for sequence in sequences}
    failed_total = sum(value in FAILURES for value in outcome.values()) or 1
    completed_total = sum(value == SUCCESS for value in outcome.values()) or 1
    by_mode: defaultdict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_mode[finding.mode].append(finding)
    rows: list[dict[str, Any]] = []
    for mode, members in by_mode.items():
        cases = {finding.case_id for finding in members}
        failed = sum(outcome.get(case) in FAILURES for case in cases)
        completed = sum(outcome.get(case) == SUCCESS for case in cases)
        rows.append({
            "mode": mode,
            "category": MODES[mode].category,
            "source": MODES[mode].source,
            "sources": dict(Counter(finding.source for finding in members)),
            "findings": len(members),
            "cases": len(cases),
            "failed_cases": failed,
            "completed_cases": completed,
            "failed_share": round(failed / failed_total, 3),
            "completed_share": round(completed / completed_total, 3),
            "examples": [f"{f.case_id}#{f.action_index}: {f.evidence}" for f in members[:examples]],
        })  # fmt: skip
    rows.sort(key=lambda row: (-row["cases"], row["mode"]))
    return rows


def critical_table(steps: Sequence[CriticalStep], *, examples: int = 3) -> list[dict[str, Any]]:
    """Per critical-step category: cases, the states they most often follow, and examples."""
    by_category: defaultdict[str, list[CriticalStep]] = defaultdict(list)
    for step in steps:
        by_category[step.category].append(step)
    rows: list[dict[str, Any]] = []
    for category, members in by_category.items():
        rows.append({
            "category": category,
            "cases": len(members),
            "share": round(len(members) / len(steps), 3),
            "outcomes": dict(Counter(step.outcome for step in members).most_common()),
            "labels": [label for label, _ in Counter(step.label for step in members).most_common(3)],
            "states_before": [name for name, _ in Counter(step.state_before for step in members).most_common(3)],
            "examples": [f"{step.case_id}#{step.action_index}: {step.sample[:80]}" for step in members[:examples]],
        })  # fmt: skip
    rows.sort(key=lambda row: -row["cases"])
    return rows


# --- gold set and evaluation -------------------------------------------------------------------------------------

GOLD_COLUMNS = ["case_id", "session_id", "outcome", "rule_critical_step", "rule_category", "rule_modes",
                "judge_critical_step", "judge_category", "judge_modes", "gold_critical_step", "gold_category",
                "gold_modes", "notes"]  # fmt: skip


def write_gold_template(path: Path, sequences: Sequence[CaseSequence], findings: Sequence[Finding],
                        steps: Sequence[CriticalStep], verdicts: dict[str, Any] | None = None, *,
                        size: int = 50) -> Path:  # fmt: skip
    """A CSV to hand-label: the failed cases (up to ``size``) with what rules and judge said; fill the ``gold_``
    columns (a step index, a category, modes separated by ``;``) and pass it to ``evaluate``."""
    critical = {step.case_id: step for step in steps}
    modes: defaultdict[str, set[str]] = defaultdict(set)
    for finding in findings:
        if finding.source == "rules":
            modes[finding.case_id].add(finding.mode)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=GOLD_COLUMNS)
        writer.writeheader()
        for sequence in [s for s in sequences if s.outcome in FAILURES][:size]:
            case_id = sequence.case.case_id
            step = critical.get(case_id)
            verdict = (verdicts or {}).get(case_id)
            writer.writerow({
                "case_id": case_id, "session_id": sequence.case.session_id, "outcome": sequence.outcome,
                "rule_critical_step": step.action_index if step else "", "rule_category": step.category if step else "",
                "rule_modes": ";".join(sorted(modes[case_id])),
                "judge_critical_step": verdict.critical_step if verdict else "",
                "judge_category": verdict.category if verdict else "",
                "judge_modes": ";".join(sorted({f.mode for f in verdict.findings})) if verdict else "",
                "gold_critical_step": "", "gold_category": "", "gold_modes": "", "notes": "",
            })  # fmt: skip
    return path


def kappa(first: Sequence[bool], second: Sequence[bool]) -> float | None:
    """Cohen's κ of two binary labellings; None when it is undefined (one label only on both sides)."""
    total = len(first)
    if not total:
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / total
    p1, p2 = sum(first) / total, sum(second) / total
    expected = p1 * p2 + (1 - p1) * (1 - p2)
    return None if expected == 1 else round((observed - expected) / (1 - expected), 3)


def evaluate(path: Path) -> dict[str, Any]:
    """How rules and judge agree with a hand-labelled gold set (``write_gold_template``, ``gold_`` columns filled):
    critical-step accuracy exactly and within ±1 and ±3 steps, category accuracy, and κ per failure mode."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if (row.get("gold_critical_step") or "").strip()]
    result: dict[str, Any] = {"labelled": len(rows)}
    for who in ("rule", "judge"):
        pairs = [(int(row[f"{who}_critical_step"]), int(row["gold_critical_step"])) for row in rows
                 if (row.get(f"{who}_critical_step") or "").strip().lstrip("-").isdigit()]  # fmt: skip
        categories = [(row[f"{who}_category"], row["gold_category"]) for row in rows
                      if row.get(f"{who}_category") and row.get("gold_category")]  # fmt: skip
        modes = sorted({m for row in rows for column in (f"{who}_modes", "gold_modes")
                        for m in (row.get(column) or "").split(";") if m})  # fmt: skip
        per_mode = {}
        for mode in modes:
            predicted = [mode in (row.get(f"{who}_modes") or "").split(";") for row in rows]
            gold = [mode in (row.get("gold_modes") or "").split(";") for row in rows]
            per_mode[mode] = {"predicted": sum(predicted), "gold": sum(gold), "kappa": kappa(predicted, gold)}
        result[who] = {
            "cases": len(pairs),
            "step_exact": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
            "step_within_1": round(sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs), 3) if pairs else None,
            "step_within_3": round(sum(abs(a - b) <= 3 for a, b in pairs) / len(pairs), 3) if pairs else None,
            "category_accuracy": round(sum(a == b for a, b in categories) / len(categories), 3) if categories
            else None,
            "modes": per_mode,
        }  # fmt: skip
    return result


@dataclass
class FailureReport:
    """Findings, critical steps and their tables, for a set of case sequences."""

    findings: list[Finding]
    critical_steps: list[CriticalStep]
    modes: list[dict[str, Any]]
    critical: list[dict[str, Any]]

    @classmethod
    def build(cls, sequences: Sequence[CaseSequence], extra: Sequence[Finding] = ()) -> FailureReport:
        findings = detect(sequences)
        by_case: defaultdict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            by_case[finding.case_id].append(finding)
        steps = [step for s in sequences if (step := critical_step(s, by_case[s.case.case_id])) is not None]
        everything = [*findings, *extra]
        return cls(everything, steps, mode_table(everything, sequences), critical_table(steps))

    def by_case(self) -> dict[str, list[Finding]]:
        grouped: defaultdict[str, list[Finding]] = defaultdict(list)
        for finding in self.findings:
            grouped[finding.case_id].append(finding)
        return dict(grouped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "modes": self.modes,
            "critical_steps": self.critical,
            "findings": [finding.to_dict() for finding in self.findings],
            "cases": [step.to_dict() for step in self.critical_steps],
        }


def drift_by(sequences: Sequence[CaseSequence], findings: Sequence[Finding], key: Callable[[CaseSequence], str | None],
             *, modes: int = 4) -> list[dict[str, Any]]:  # fmt: skip
    """Completion and the most common failure modes per group of cases (``key``: e.g. the agent revision a case ran
    under): whether a change to the configuration moved the failures it was meant to."""
    groups: defaultdict[str, list[CaseSequence]] = defaultdict(list)
    for sequence in sequences:
        groups[key(sequence) or "unknown"].append(sequence)
    cases_with: defaultdict[str, set[str]] = defaultdict(set)
    for finding in findings:
        cases_with[finding.mode].add(finding.case_id)
    common = [mode for mode, _ in Counter(f.mode for f in findings).most_common(modes)]
    rows: list[dict[str, Any]] = []
    for name, members in groups.items():
        ids = {member.case.case_id for member in members}
        starts = sorted(member.case.started_at for member in members if member.case.started_at)
        rows.append({
            "key": name,
            "cases": len(members),
            "from": (starts[0] or "")[:10] if starts else "",
            "completed": round(sum(member.success for member in members) / len(members), 3),
            "modes": {mode: round(len(cases_with[mode] & ids) / len(members), 3) for mode in common},
        })  # fmt: skip
    rows.sort(key=lambda row: row["from"])
    return rows


def _when(at: str | None) -> str:
    return f" at {at[:19].replace('T', ' ')}" if at else ""


def focus(findings: Sequence[Finding], step: CriticalStep | None, *, limit: int = 8) -> str:
    """A session's findings and critical step as short text for a reflector: where to look, flagged as hints, located
    by time and by what was called (the reflector's transcript has no step numbers)."""
    if not findings and step is None:
        return ""
    lines = [("Automated rules (thearc's session analysis) flagged the following in this session. They are hints "
              "that may be false positives: confirm each against the transcript before relying on it. When the "
              "configuration contributed to one of these failures, or failed to prevent it, rate the cited lines' "
              "failure_mode with it.")]  # fmt: skip
    if step is not None:
        lines.append(f"- Critical step{_when(step.at)} ({step.category}, the run ended {step.outcome}): "
                     f"{step.label} {step.sample[:120]!r}")  # fmt: skip
    lines += [f"- {f.mode}{_when(f.at)}: {MODES[f.mode].definition} Evidence: {f.evidence}"
              for f in findings[:limit]]  # fmt: skip
    return "\n".join(lines)
