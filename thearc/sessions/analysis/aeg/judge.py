"""An optional LLM judge of failed cases, run only on windows the rules chose.

Rules choose the windows: each failed case's critical step with about 15 steps before it and 5 after, the task and
the final answer. A sample of completed cases is judged too, to calibrate. The judge reads the window and the rules'
findings (labelled as possible false positives, as AgentRx does), answers a yes/no checklist, and returns one JSON
object that is checked before it is accepted: steps must be in the window, modes in the taxonomy, and every quote must
appear in the window text, which rejects invented evidence. A rejected answer goes back once with the reasons.
Findings the judge marks unclear, or gives no quote for, are dropped. Answers are cached by the window, the prompt
version and the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .failures import CRITICAL_CATEGORIES, MODES, TAXONOMY, CriticalStep, Finding, critical_step
from .graph import FAILURES, CaseSequence

PROMPT_VERSION = "judge-v1"
BEFORE, AFTER = 15, 5
_OUTCOMES = ("failed", "completed", "unclear")
_CONFIDENCE = ("high", "medium", "low")

CHECKLIST = """Answer these for yourself before you decide (they are not part of the answer):
1. Did the agent understand the task and its constraints?
2. Was its plan reasonable, and did it follow it?
3. Did each action match what the agent said it would do?
4. Did it read and act on what tool results and the user told it?
5. Did it run a check after its last change, and did that check pass?
6. Does the final answer's claim match the evidence in the steps?"""


@dataclass
class Window:
    """What the judge reads about a case."""

    case_id: str
    outcome: str
    first: int
    last: int
    text: str
    critical: CriticalStep | None
    findings: list[Finding]
    steps: dict[int, tuple[str | None, list[str]]] = field(default_factory=dict)  # Step: when, and its event IDs.

    @property
    def tokens(self) -> int:
        return len(self.text) // 4


@dataclass
class Verdict:
    """The judge's accepted answer for one case."""

    case_id: str
    critical_step: int | None
    category: str | None
    outcome: str
    findings: list[Finding] = field(default_factory=list)
    attempts: int = 1
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "critical_step": self.critical_step, "category": self.category,
                "outcome": self.outcome, "findings": [finding.to_dict() for finding in self.findings],
                "attempts": self.attempts, "cached": self.cached}  # fmt: skip


def _line(index: int, sequence: CaseSequence) -> str:
    action = sequence.actions[index]
    return f"#{index} {action.label}  {action.sample.replace(chr(10), ' ')[:220]}"


def _text_of(sequence: CaseSequence, kind: str, last: bool = False) -> str:
    events = [event for event in sequence.case.events if event.kind == kind and event.text.strip()]
    if not events:
        return ""
    return (events[-1] if last else events[0]).text.strip()[:1500]


def window(sequence: CaseSequence, findings: Sequence[Finding] = (), *, before: int = BEFORE,
           after: int = AFTER) -> Window:  # fmt: skip
    """The judge's view of a case: its task, the steps around the critical step (the last ones for a completed case),
    and its final answer."""
    step = critical_step(sequence, findings)
    actions = sequence.actions
    centre = step.action_index if step is not None else len(actions) - 1 - after
    first, last = max(0, centre - before), min(len(actions) - 1, centre + after)
    lines = [f"Task (the user's first message):\n{_text_of(sequence, 'user_msg') or '(none recorded)'}", "",
             f"Steps {first}–{last} of {len(actions)} (label, then what was called and the first error line):"]
    lines += [_line(index, sequence) for index in range(first, last + 1)]
    final = _text_of(sequence, "assistant_text", last=True)
    lines += ["", f"Final answer:\n{final or '(none)'}"]
    steps = {index: (actions[index].start, list(actions[index].event_ids)) for index in range(first, last + 1)}
    return Window(sequence.case.case_id, sequence.outcome, first, last, "\n".join(lines), step, list(findings), steps)


def prompt(view: Window) -> str:
    modes = "\n".join(f"- {mode.id} ({mode.category}): {mode.definition}" for mode in TAXONOMY)
    flagged = "\n".join(f"- {f.mode} at step {f.action_index}: {f.evidence}" for f in view.findings) or "- none"
    return f"""You are auditing one run of a coding agent for failures. Judge only from the window below.

Failure modes:
{modes}

Critical-step categories: {", ".join(CRITICAL_CATEGORIES)}.
The critical step is the first failure the run never recovered from; for a run that succeeded it is null.

Automated rules flagged these. They may be false positives: confirm or ignore each.
{flagged}

{CHECKLIST}

<window>
{view.text}
</window>

Answer with one JSON object and nothing else:
{{"critical_step": <step number in {view.first}..{view.last} or null>,
  "category": <critical-step category or null>,
  "outcome": "failed" | "completed" | "unclear",
  "findings": [{{"mode": <failure mode>, "step_ids": [<step numbers>], "quote": <exact text copied from the window>,
                "confidence": "high" | "medium" | "low", "counterevidence": <what argues against it, or "">}}]}}
Report a finding only with a quote copied exactly from the window. If you are unsure, leave it out."""


def _normal(text: str) -> str:
    return " ".join(text.split()).lower()


def check(answer: str, view: Window) -> tuple[dict[str, Any] | None, list[str]]:
    """The parsed answer and the problems with it (none when it is acceptable)."""
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError as error:
        return None, [f"not valid JSON: {error}"]
    if not isinstance(data, dict):
        return None, ["answer with one JSON object"]
    problems = []
    step = data.get("critical_step")
    if step is not None and (not isinstance(step, int) or not view.first <= step <= view.last):
        problems.append(f"critical_step {step!r} is not a step number in {view.first}..{view.last}")
    if data.get("category") is not None and data.get("category") not in CRITICAL_CATEGORIES:
        problems.append(f"category {data.get('category')!r} is not one of {list(CRITICAL_CATEGORIES)}")
    if data.get("outcome") not in _OUTCOMES:
        problems.append(f"outcome must be one of {list(_OUTCOMES)}")
    text = _normal(view.text)
    findings = data.get("findings") or []
    if not isinstance(findings, list):
        problems.append("findings must be a list")
        findings = []
    for number, item in enumerate(findings):
        if not isinstance(item, dict):
            problems.append(f"findings[{number}] must be an object")
            continue
        if item.get("mode") not in MODES:
            problems.append(f"findings[{number}].mode {item.get('mode')!r} is not a failure mode of the list")
        steps = item.get("step_ids") or []
        if not isinstance(steps, list) or not all(isinstance(s, int) and view.first <= s <= view.last
                                                  for s in steps):  # fmt: skip
            problems.append(f"findings[{number}].step_ids must be step numbers in {view.first}..{view.last}")
        quote = item.get("quote")
        if quote and _normal(str(quote)) not in text:
            problems.append(f"findings[{number}].quote is not in the window; copy it exactly")
        if item.get("confidence") not in _CONFIDENCE:
            problems.append(f"findings[{number}].confidence must be one of {list(_CONFIDENCE)}")
    return data, problems


class FailureJudge:
    """Judges windows with a model: ``complete(prompt) -> answer`` makes the call (default ``claude -p``)."""

    def __init__(self, complete: Callable[[str], str] | None = None, *, cache: Path | None = None,
                 model: str = "claude-sonnet-5-5",
                 on_estimate: Callable[[dict[str, int]], None] | None = None) -> None:  # fmt: skip
        self.complete = complete or self._claude
        self.on_estimate = on_estimate
        self.cache_path, self.model = cache, model
        self.cache: dict[str, Any] = {}
        self.calls = 0
        self.failures: list[str] = []
        if cache is not None and cache.exists():
            self.cache = json.loads(cache.read_text(encoding="utf-8"))

    def _claude(self, text: str) -> str:
        completed = subprocess.run(["claude", "-p", "--model", self.model], input=text, capture_output=True,
                                   text=True, timeout=600, check=False)  # fmt: skip
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"claude exited with {completed.returncode}")
        return completed.stdout

    def _key(self, asked: str) -> str:
        return hashlib.sha256(f"{PROMPT_VERSION}\0{self.model}\0{asked}".encode()).hexdigest()[:24]

    def judge(self, view: Window) -> Verdict | None:
        """The verdict on one window, or None when the model gave no acceptable answer (twice)."""
        asked = prompt(view)
        key = self._key(asked)
        cached = key in self.cache
        if cached:
            data, attempts = self.cache[key]["answer"], self.cache[key]["attempts"]
        else:
            data, attempts = None, 0
            text = asked
            for attempts in (1, 2):
                try:
                    self.calls += 1
                    answer = self.complete(text)
                except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                    self.failures.append(str(error)[:200])
                    return None
                data, problems = check(answer, view)
                if not problems:
                    break
                text = (f"{asked}\n\nYour previous answer was rejected:\n" + "\n".join(f"- {p}" for p in problems)
                        + f"\n\nPrevious answer:\n{answer[:4000]}\n\nAnswer again, fixing every problem.")  # fmt: skip
                data = None
            if data is None:
                self.failures.append(f"{view.case_id}: no acceptable answer")
                return None
            self.cache[key] = {"answer": data, "attempts": attempts}
            self._save()
        findings = []
        for item in data.get("findings") or []:
            if not item.get("quote") or data.get("outcome") == "unclear":
                continue  # Without evidence quoted, or on an unclear run: no finding.
            steps = [s for s in item.get("step_ids") or [] if isinstance(s, int)]
            evidence = f"{str(item['quote'])[:200]!r}"
            if item.get("counterevidence"):
                evidence += f" (against: {str(item['counterevidence'])[:100]})"
            index = steps[0] if steps else view.first
            ids = [event_id for step in steps for event_id in view.steps.get(step, (None, []))[1]]
            findings.append(Finding(item["mode"], view.case_id, index, ids, evidence, source="judge",
                                    confidence=item.get("confidence", "low"),
                                    at=view.steps.get(index, (None, []))[0]))  # fmt: skip
        return Verdict(view.case_id, data.get("critical_step"), data.get("category"), data["outcome"], findings,
                       attempts, cached)  # fmt: skip

    def judge_all(self, windows: Sequence[Window]) -> list[Verdict]:
        """Verdicts on ``windows`` (those the model answered), after reporting the estimate to ``on_estimate``."""
        fresh = [view for view in windows if self._key(prompt(view)) not in self.cache]
        if self.on_estimate is not None:
            self.on_estimate({**estimate(fresh), "windows": len(windows), "cached": len(windows) - len(fresh)})
        return [verdict for view in windows if (verdict := self.judge(view)) is not None]

    def _save(self) -> None:
        if self.cache_path is not None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=0, sort_keys=True), encoding="utf-8")


def choose(sequences: Sequence[CaseSequence], by_case: dict[str, list[Finding]], *, limit: int = 20,
           calibration: float = 0.2) -> list[Window]:  # fmt: skip
    """Windows to judge: failed cases first (those with findings before the rest), then a share of completed cases
    for calibration, ``limit`` in all."""
    failed = [s for s in sequences if s.outcome in FAILURES and s.actions]
    failed.sort(key=lambda s: -len(by_case.get(s.case.case_id, [])))
    completed = [s for s in sequences if s.outcome not in FAILURES and s.actions]
    completed.sort(key=lambda s: -len(by_case.get(s.case.case_id, [])))
    calibrate = min(len(completed), max(1, round(limit * calibration))) if completed else 0
    chosen = failed[: limit - calibrate] + completed[:calibrate]
    return [window(s, by_case.get(s.case.case_id, [])) for s in chosen]


def estimate(windows: Sequence[Window], *, answer_tokens: int = 400) -> dict[str, int]:
    """About how many tokens judging ``windows`` takes (prompt and answer, before any retry)."""
    asked = sum(len(prompt(view)) // 4 for view in windows)
    return {"windows": len(windows), "input_tokens": asked, "output_tokens": answer_tokens * len(windows),
            "total_tokens": asked + answer_tokens * len(windows)}  # fmt: skip


def agreement(verdicts: Sequence[Verdict], rules: dict[str, list[Finding]],
              sequences: Sequence[CaseSequence]) -> dict[str, Any]:  # fmt: skip
    """Where rules and judge agree: per mode (both, rules only, judge only), on the critical step, and on the
    outcome (the judge's against the heuristic one)."""
    outcome = {s.case.case_id: s.outcome for s in sequences}
    critical = {}
    for sequence in sequences:
        step = critical_step(sequence, rules.get(sequence.case.case_id, []))
        if step is not None:
            critical[sequence.case.case_id] = step
    per_mode: dict[str, Counter[str]] = {}
    steps: list[int] = []
    outcomes: Counter[str] = Counter()
    for verdict in verdicts:
        rule_modes = {f.mode for f in rules.get(verdict.case_id, [])}
        judge_modes = {f.mode for f in verdict.findings}
        for mode in rule_modes | judge_modes:
            counts = per_mode.setdefault(mode, Counter())
            counts["both" if mode in rule_modes and mode in judge_modes else
                   "rules_only" if mode in rule_modes else "judge_only"] += 1  # fmt: skip
        if verdict.critical_step is not None and verdict.case_id in critical:
            steps.append(abs(verdict.critical_step - critical[verdict.case_id].action_index))
        heuristic = "failed" if outcome.get(verdict.case_id) in FAILURES else "completed"
        outcomes["unclear" if verdict.outcome == "unclear" else
                 "agree" if verdict.outcome == heuristic else "disagree"] += 1  # fmt: skip
    rows = [{"mode": mode, "both": c["both"], "rules_only": c["rules_only"], "judge_only": c["judge_only"]}
            for mode, c in sorted(per_mode.items(), key=lambda kv: -sum(kv[1].values()))]  # fmt: skip
    return {
        "cases": len(verdicts),
        "outcome": dict(outcomes),
        "critical_step": {
            "compared": len(steps),
            "exact": round(sum(d == 0 for d in steps) / len(steps), 3) if steps else None,
            "within_3": round(sum(d <= 3 for d in steps) / len(steps), 3) if steps else None,
        },
        "modes": rows,
    }
