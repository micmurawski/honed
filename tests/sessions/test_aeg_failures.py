"""Failure modes, critical steps, the LLM judge, the gold set, and the failure-mode scale in reflections."""

import csv
import json

from click.testing import CliRunner

from thearc.cli import main
from thearc.evolve.round import release_at
from thearc.sessions import Session
from thearc.sessions.analysis.aeg import ExecutionGraphView, export_execution_graph, focus, session_failures
from thearc.sessions.analysis.aeg.events import cases_from_threads
from thearc.sessions.analysis.aeg.failures import (
    MODES,
    TAXONOMY,
    FailureReport,
    critical_step,
    detect,
    evaluate,
    failure_mode_scale,
    kappa,
)
from thearc.sessions.analysis.aeg.graph import CaseSequence, outcome
from thearc.sessions.analysis.aeg.judge import FailureJudge, agreement, check, choose, window
from thearc.sessions.analysis.aeg.labels import label_case
from thearc.sessions.analysis.aeg.states import abstract

from . import test_aeg
from .test_aeg import call, event, result, threads

store = test_aeg.store  # The fixture: sessions alike but for a final failing test in half of them.


def sequence_of(*events):
    session = Session.from_events([event(0, role="user", text="Fix the retry bug in the client please"), *events])
    (case,) = cases_from_threads(session.root_threads)
    actions = label_case(case)
    return CaseSequence(case, actions, abstract(actions, "phase+outcome"), outcome(case, actions))


def modes(sequence):
    return [finding.mode for finding in detect([sequence])]


def test_finishing_on_a_failing_check_is_premature_and_a_success_claim_contradicts_it():
    sequence = sequence_of(call(1, "sed -i s/a/b/ src/app.py", "a"), result(2, "a"),
                           call(3, "pytest -q", "b"), result(4, "b", failed=True, text="FAILED test_retry"),
                           event(5, text="All tests pass now."))  # fmt: skip
    found = detect([sequence])
    assert {f.mode for f in found} == {"premature_done", "claim_contradicted"}
    claim = next(f for f in found if f.mode == "claim_contradicted")
    assert "all tests pass" in claim.evidence.lower() and claim.action_index == 3 and claim.at
    assert set(claim.event_ids) >= {"e3", "e4", "e5"}
    honest = sequence_of(call(1, "sed -i s/a/b/ src/app.py", "a"), result(2, "a"), call(3, "pytest -q", "b"),
                         result(4, "b", failed=True), event(5, text="The test still fails; I need your input."))
    assert modes(honest) == ["premature_done"]  # No success claimed: no contradiction.


def test_no_verification_after_the_last_edit():
    assert modes(sequence_of(call(1, "pytest -q", "a"), result(2, "a"), call(3, "sed -i s/a/b/ src/app.py", "b"),
                             result(4, "b"), event(5, text="Done."))) == ["no_verification"]  # fmt: skip
    # Docs-only edits need no check, and a check after the edit is verification.
    assert modes(sequence_of(call(1, "sed -i s/a/b/ README.md", "b"), result(2, "b"), event(3, text="Done."))) == []
    assert modes(sequence_of(call(1, "sed -i s/a/b/ src/app.py", "b"), result(2, "b"), call(3, "pytest", "c"),
                             result(4, "c"), event(5, text="Done."))) == []  # fmt: skip


def test_incorrect_verification_repetition_and_clarification():
    corrected = sequence_of(call(1, "sed -i s/a/b/ src/app.py", "a"), result(2, "a"), call(3, "pytest", "b"),
                            result(4, "b"), event(5, text="Done."),
                            event(6, role="user", text="No, that is wrong: retries must back off"))  # fmt: skip
    assert "incorrect_verification" in modes(corrected)
    looping = sequence_of(*(e for n in range(3) for e in (call(1 + 2 * n, "pytest -q", f"c{n}"),
                                                          result(2 + 2 * n, f"c{n}", failed=True))))  # fmt: skip
    (repeated,) = [f for f in detect([looping]) if f.mode == "step_repetition"]
    assert "3×" in repeated.evidence and repeated.action_index == 1
    unclear = Session.from_events([event(0, role="user", text="make it faster"),
                                   call(1, "sed -i s/a/b/ src/app.py", "a"), result(2, "a"),
                                   event(3, text="Done."), event(4, role="user", text="No, I meant the build")])
    (case,) = cases_from_threads(unclear.root_threads)
    actions = label_case(case)
    found = detect([CaseSequence(case, actions, abstract(actions, "phase"), outcome(case, actions))])
    assert "failed_to_clarify" in {f.mode for f in found}


def test_tool_failures_are_named_from_their_output():
    sequence = sequence_of(call(1, "pytest", "a"), result(2, "a", failed=True, text="<tool_use_error>bad input"),
                           call(3, "rm -rf build", "b"), result(4, "b", failed=True, text="Permission denied"),
                           call(5, "curl x", "c"), result(6, "c", failed=True, text="Error: request timed out"))
    assert modes(sequence) == ["invalid_invocation", "guardrail_block", "system_failure"]


def test_the_critical_step_is_the_first_failure_never_recovered_from():
    recovered_then_stuck = sequence_of(
        call(1, "pytest", "a"), result(2, "a", failed=True),  # Recovered by the passing run below.
        call(3, "sed -i s/a/b/ src/app.py", "b"), result(4, "b"),
        call(5, "pytest", "c"), result(6, "c"),
        call(7, "sed -i s/b/c/ src/app.py", "d"), result(8, "d"),
        call(9, "python -m build", "e"), result(10, "e", failed=True),  # The final run of failures starts here.
        call(11, "pytest", "f"), result(12, "f", failed=True),
    )
    step = critical_step(recovered_then_stuck)
    assert step.label == "BUILD:fail" and step.category == "check_failed" and step.outcome == "abandoned"
    assert step.state_before == "EDIT"
    completed = sequence_of(call(1, "pytest", "a"), result(2, "a"), event(3, text="Done."))
    assert critical_step(completed) is None
    aborted = sequence_of(call(1, "cat src/app.py", "a"), result(2, "a"), event(3, text="I will rewrite it."),
                          event(4, role="user", text="[Request interrupted by user]"))  # fmt: skip
    assert critical_step(aborted).category == "user_intervention"
    silent = sequence_of(call(1, "cat src/app.py", "a"), result(2, "a"))
    assert critical_step(silent).category == "no_final_answer"


def test_delegation_failed_needs_the_child_thread(store):
    parent = Session.from_events([
        event(0, role="user", text="Fix it", run="p"),
        event(1, kind="tool_call", tool_name="Task", call_id="t", run="p", arguments={"prompt": "fix"}),
        event(2, text="Fixed, all done.", run="p"),
        event(3, role="user", text="fix", run="child", parent_run_id="p"),
        call(4, "pytest", "x", run="child", parent_run_id="p"), result(5, "x", failed=True, run="child"),
    ])  # fmt: skip
    cases = list(cases_from_threads(parent.threads))
    assert {case.parent_thread_id is None for case in cases} == {True, False}
    sequences = [CaseSequence(c, a, abstract(a, "phase"), outcome(c, a)) for c in cases if (a := label_case(c))]
    assert "delegation_failed" in {f.mode for f in detect(sequences)}


def test_tables_view_and_export(store, tmp_path):
    view = ExecutionGraphView.build(threads(store), compare=None)
    report = view.failures
    # The fixture's final answer, "Fixed it.", claims what the failing test contradicts.
    assert {row["mode"] for row in report.modes} == {"premature_done", "claim_contradicted"}
    row = next(row for row in report.modes if row["mode"] == "premature_done")
    assert row["failed_cases"] == 6 and row["completed_cases"] == 0 and row["failed_share"] == 1.0
    assert report.critical[0]["category"] == "check_failed" and report.critical[0]["cases"] == 6
    markdown = view.to_markdown()
    assert "## Failure modes" in markdown and "## Critical steps" in markdown and "premature_done" in markdown
    data = view.to_dict()
    assert data["failures"]["findings"] and data["summary"]["critical_steps"] == 6
    folder = export_execution_graph(view, tmp_path / "out")
    assert (folder / "findings.jsonl").read_text().count("\n") == len(report.findings)
    with (folder / "gold_set.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6 and rows[0]["rule_category"] == "check_failed"
    assert rows[0]["rule_modes"] == "claim_contradicted;premature_done"


def test_gold_set_evaluation_and_kappa(tmp_path):
    assert kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert kappa([True, True], [True, True]) is None
    gold = tmp_path / "gold.csv"
    gold.write_text(
        "case_id,rule_critical_step,rule_category,rule_modes,judge_critical_step,judge_category,judge_modes,"
        "gold_critical_step,gold_category,gold_modes\n"
        "a,4,check_failed,premature_done,4,check_failed,premature_done,4,check_failed,premature_done\n"
        "b,2,check_failed,premature_done,9,check_failed,,4,check_failed,\n"
        "c,7,edit_failed,,,,,,,\n"  # Not labelled: left out.
    )
    result = evaluate(gold)
    assert result["labelled"] == 2
    assert result["rule"]["step_exact"] == 0.5 and result["rule"]["step_within_3"] == 1.0
    assert result["judge"]["step_within_3"] == 0.5 and result["judge"]["category_accuracy"] == 1.0
    assert result["rule"]["modes"]["premature_done"] == {"predicted": 2, "gold": 1, "kappa": 0.0}
    assert result["judge"]["modes"]["premature_done"]["kappa"] == 1.0


def failing_sequence():
    return sequence_of(call(1, "sed -i s/a/b/ src/app.py", "a"), result(2, "a"), call(3, "pytest -q", "b"),
                       result(4, "b", failed=True, text="FAILED test_retry"), event(5, text="All tests pass now."))


def test_the_judge_checks_quotes_and_steps_retries_once_and_caches(tmp_path):
    sequence = failing_sequence()
    rules = detect([sequence])
    view = window(sequence, rules)
    assert view.critical.label == "TEST:fail" and "All tests pass now." in view.text and "#2 TEST:fail" in view.text
    _, problems = check('{"critical_step": 99, "category": "x", "outcome": "failed", "findings": [{"mode": '
                        '"premature_done", "step_ids": [2], "quote": "invented", "confidence": "high"}]}', view)
    assert len(problems) == 3  # Step out of the window, unknown category, a quote not in the window.
    answers = iter([
        '{"critical_step": 99, "category": null, "outcome": "failed", "findings": []}',
        ('{"critical_step": 2, "category": "check_failed", "outcome": "failed", "findings": ['
         '{"mode": "claim_contradicted", "step_ids": [2, 3], "quote": "All tests pass now.", "confidence": "high", '
         '"counterevidence": ""}, {"mode": "task_derailment", "step_ids": [1], "quote": "", "confidence": "low"}]}'),
    ])
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return next(answers)

    judge = FailureJudge(complete, cache=tmp_path / "judge.json")
    verdict = judge.judge(view)
    assert verdict.attempts == 2 and "was rejected" in prompts[1] and "critical_step 99" in prompts[1]
    assert verdict.critical_step == 2 and [f.mode for f in verdict.findings] == ["claim_contradicted"]  # No quote.
    assert verdict.findings[0].source == "judge" and verdict.findings[0].event_ids and verdict.findings[0].at
    assert "may be false positives" in prompts[0] and "premature_done at step" in prompts[0]
    again = FailureJudge(lambda prompt: (_ for _ in ()).throw(AssertionError("cached")), cache=tmp_path / "judge.json")
    assert again.judge(view).cached
    table = agreement([verdict], {sequence.case.case_id: rules}, [sequence])
    assert table["outcome"] == {"agree": 1} and table["critical_step"]["exact"] == 1.0
    assert {row["mode"]: row for row in table["modes"]}["claim_contradicted"]["both"] == 1


def test_the_judge_runs_in_the_view_on_chosen_windows(store):
    estimates = []
    judge = FailureJudge(lambda prompt: '{"critical_step": null, "category": null, "outcome": "unclear", '
                         '"findings": []}', on_estimate=estimates.append)  # fmt: skip
    view = ExecutionGraphView.build(threads(store), compare=None, judge=judge, judge_limit=5)
    assert len(view.verdicts) == 5 and judge.calls == 2  # The sessions are alike: identical windows are cached.
    assert estimates[0]["windows"] == 5 and estimates[0]["total_tokens"] > 0
    assert view.judge["agreement"]["outcome"] == {"unclear": 5}
    assert "## Judge" in view.to_markdown()
    sequences = view.sequences
    chosen = choose(sequences, view.failures.by_case(), limit=5)
    assert sum(w.outcome == "completed" for w in chosen) == 1  # A completed case to calibrate.


def test_drift_by_revision():
    timeline = [(__import__("datetime").datetime.fromisoformat("2026-10-06T00:00:00+00:00"), "1.0.1")]
    assert release_at(timeline, "2026-10-01T10:00:00Z") == "initial"
    assert release_at(timeline, "2026-10-07T10:00:00+00:00") == "1.0.1"


def test_drift_table_in_the_view(store):
    view = ExecutionGraphView.build(threads(store), compare=None,
                                    revision_of=lambda case: "new" if case.started_at >= "2026-10-07" else "old")
    rows = {row["key"]: row for row in view.revisions}
    assert set(rows) == {"old", "new"} and "premature_done" in rows["old"]["modes"]
    assert "### By agent revision" in view.to_markdown()


def test_focus_for_reflectors_and_the_scale(store):
    found = session_failures(store.get(record.id) for record in store.list())
    failing = [sid for sid, (findings, _) in found.items() if findings]
    assert len(failing) == 6
    text = focus(*found[failing[0]])
    assert "may be false positives" in text and "- premature_done" in text and "- Critical step (check_failed" in text
    assert focus([], None) == ""
    scale = failure_mode_scale()
    assert set(scale.labels) == set(MODES) == {mode.id for mode in TAXONOMY}
    assert scale.validate("premature_done") == "premature_done"


def test_the_reflector_gets_the_focus(monkeypatch):
    from thearc.reflection.reflector import Reflector

    seen = {}

    def run(self, task, agent, schema, **kw):
        seen["task"], seen["evidence"] = task, kw["evidence"]
        return "done"

    monkeypatch.setattr(Reflector, "_run", run)
    reflector = Reflector.__new__(Reflector)
    installed = type("Installed", (), {"session_id": "x", "path": __file__, "transformations": ()})()
    history = Session.from_events([event(0, role="user", text="Fix it")])
    Reflector._handoff(reflector, "Review.", history, installed, None, _schema(), None, {}, __import__(
        "collections").Counter(), "- premature_done at 2026-10-01 10:00:05")  # fmt: skip
    assert seen["task"].endswith("## Where to look\n\n- premature_done at 2026-10-01 10:00:05")
    assert seen["evidence"]["focus"].startswith("- premature_done")


def _schema():
    from thearc.evolve import SCHEMA

    return SCHEMA


def test_cli_judge_revisions_and_gold(store, tmp_path, monkeypatch):
    runner = CliRunner()
    index = str(store.path)
    selected = runner.invoke(main, ["sessions", "--index", index, "query", "--json", "select:session"]).output

    def graph(*args):
        return runner.invoke(main, ["sessions", "--index", index, "graph", "aeg", "--compare", "none", *args],
                             input=selected)  # fmt: skip

    export = tmp_path / "aeg"
    exported = graph("--export", str(export))
    assert exported.exit_code == 0, exported.output
    gold = export / "aeg" / "gold_set.csv"
    rows = list(csv.DictReader(gold.open()))
    for row in rows:
        row.update(gold_critical_step=row["rule_critical_step"], gold_category="check_failed",
                   gold_modes="premature_done")  # fmt: skip
    with gold.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    scored = graph("-o", "json", "--gold", str(gold))
    assert scored.exit_code == 0, scored.output
    assert json.loads(scored.stdout)["gold_evaluation"]["rule"]["step_exact"] == 1.0
    missing = graph("--revisions", str(tmp_path))
    assert missing.exit_code != 0 and "no releases found" in missing.output


def test_failure_report_accepts_extra_findings(store):
    view = ExecutionGraphView.build(threads(store)[:2], compare=None)
    extra = [f for f in view.failures.findings][:1]
    report = FailureReport.build(view.sequences, extra)
    assert len(report.findings) == len(view.failures.findings) + len(extra)
