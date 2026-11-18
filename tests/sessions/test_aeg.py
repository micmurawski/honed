import json

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.sessions import Event, Session, Sessions, SourceReference
from thearc.sessions.analysis.aeg import ExecutionGraphView, export_execution_graph
from thearc.sessions.analysis.aeg.events import cases_from_threads, truncate_output
from thearc.sessions.analysis.aeg.graph import CaseSequence, aggregate, outcome
from thearc.sessions.analysis.aeg.labels import (
    LLMLabeler,
    assistant_kind,
    command_family,
    file_kind,
    label_case,
    significant_part,
    user_intent,
)
from thearc.sessions.analysis.aeg.mining import contrast, happy_path, loop_instances, loops, prefixspan
from thearc.sessions.analysis.aeg.states import abstract, check_abstraction, coarsen

from .test_sessions import write_jsonl

# --- rules ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "family"),
    [
        ("pytest -q", "test"),
        ("cd app && uv run python -m pytest tests", "test"),
        ("grep -n x f.py; python -m pytest -x", "test"),
        ("ruff check .", "build"),
        ("pip install requests", "install"),
        ("sed -i 's/a/b/' x.py", "edit"),
        ("git diff | head", "git"),
        ("python script.py", "run"),
        ("ls -la && cat README.md", "search"),
        ("FOO=1 timeout 5 echo hi", "other"),
    ],
)
def test_command_families(command, family):
    assert command_family(command) == family


def test_significant_part_and_file_kinds():
    assert significant_part("cd app && grep x f.py; pytest -q") == "pytest -q"
    assert [file_kind(p) for p in ("src/app.py", "tests/test_a.py", "pyproject.toml", "README.md", "logo.png")] == [
        "src",
        "test",
        "config",
        "docs",
        "other",
    ]


@pytest.mark.parametrize(
    ("text", "first", "intent"),
    [
        ("Fix the retry bug", True, "TASK_NEW"),
        ("[Request interrupted by user]", False, "ABORT"),
        ("No, use the other file instead", False, "CORRECTION"),
        ("ok", False, "APPROVE"),
        ("continue", False, "CONTINUE"),
        ("Can we also add a test for it", False, "TASK_NEW"),
        ("why is this slow?", False, "QUESTION"),
        ("the file is in src/app", False, "CLARIFY"),
    ],
)
def test_user_intents(text, first, intent):
    assert user_intent(text, first=first) == intent


def test_assistant_kinds_and_truncation():
    assert assistant_kind("Done, all tests pass.", last=True) == "FINAL_SUMMARY"
    assert assistant_kind("Should I also update the docs?", last=False) == "ASK_USER"
    assert assistant_kind("I'll start by reading the router.", last=False) == "PLAN"
    head, errors = truncate_output("ok\n" * 400 + "Traceback (most recent call last):\nValueError: bad")
    assert len(head) == 500 and "ValueError: bad" in errors


# --- events, labels, states ----------------------------------------------------------------------------------------


def event(number, *, kind="message", role="assistant", text="", run="r", native=None, parent=None, raw=None, **kw):
    return Event(
        id=f"e{number}",
        source_id="fixture",
        harness="pi",
        session_id="s",
        run_id=run,
        kind=kind,
        role=role,
        text=text,
        native_id=native,
        parent_native_id=parent,
        timestamp=f"2026-10-01T10:00:{number:02d}+00:00",
        raw=raw or {},
        reference=SourceReference(path="/f.jsonl", generation=0, byte_offset=number * 100, byte_length=100, line=1),
        **kw,
    )


def call(number, command, call_id, **kw):
    return event(number, kind="tool_call", tool_name="bash", action_kind="shell.exec", call_id=call_id,
                 arguments={"command": command}, **kw)  # fmt: skip


def result(number, call_id, *, failed=False, text="ok", **kw):
    return event(number, kind="tool_result", role="tool", call_id=call_id, status="failure" if failed else None,
                 text=text, **kw)  # fmt: skip


def case_of(events):
    session = Session.from_events(events)
    (case,) = cases_from_threads(session.root_threads)
    return case


def test_tree_shaped_sessions_become_one_case_per_branch():
    events = [
        event(0, role="user", text="Fix it", native="a"),
        call(1, "pytest", "c1", native="b", parent="a"),
        result(2, "c1", failed=True, native="c", parent="b"),
        event(3, text="Trying another way", native="d", parent="c"),  # Abandoned branch.
        event(4, text="Fixed with the other approach.", native="e", parent="c"),  # Main branch: last in the stream.
    ]
    session = Session.from_events(events)
    cases = list(cases_from_threads(session.root_threads))
    assert len(cases) == 2
    assert [c.abandoned_branch for c in cases] == [True, False]
    assert [e.text for e in cases[1].events][-1] == "Fixed with the other approach."
    assert all("Trying another way" not in e.text for e in cases[1].events)


def test_usage_is_counted_once_per_model_reply():
    reply = {"id": "m1", "message": {"model": "minimax", "usage": {"input": 100, "output": 10, "cost": {"total": 0.5}}}}
    case = case_of([event(0, role="user", text="Go"), event(1, text="Reading.", raw=reply),
                    call(2, "ls", "c1", raw=reply), result(3, "c1")])  # fmt: skip
    assert case.tokens == 110 and case.cost == 0.5 and case.model == "minimax"


def test_labels_join_calls_with_their_results_and_skip_duplicates():
    case = case_of([
        event(0, role="user", text="Fix it"),
        event(1, role="user", text="Fix it"),  # The same prompt recorded twice.
        call(2, "pytest -q", "c1"),
        result(3, "c1", failed=True, text="Exit code 1\nFAILED test_a"),
        call(4, "pytest -q", "c1"),  # The same call recorded twice.
        call(5, "sed -i s/a/b/ src/app.py", "c2"),
        result(6, "c2"),
        call(7, "pytest -q", "c3"),
        result(8, "c3", text="1 passed"),
        event(9, text="Fixed: the retry limit was off by one."),
    ])  # fmt: skip
    labels = [action.label for action in label_case(case)]
    assert labels == ["USER:TASK_NEW", "TEST:fail", "EDIT:src", "TEST:ok", "FINAL_SUMMARY"]


def test_abstractions_collapse_repeats_and_derive_debug():
    case = case_of([
        event(0, role="user", text="Fix it"),
        call(1, "pytest", "c1"), result(2, "c1", failed=True),
        call(3, "cat src/a.py", "c2"), result(4, "c2"),
        call(5, "cat src/b.py", "c3"), result(6, "c3"),
        call(7, "sed -i s/a/b/ src/a.py", "c4"), result(8, "c4"),
        call(9, "pytest", "c5"), result(10, "c5"),
        event(11, text="Done."),
    ])  # fmt: skip
    actions = label_case(case)
    states = abstract(actions, "phase+outcome")
    assert [(s.name, s.repeat) for s in states] == [
        ("USER_TURN:TASK_NEW", 1), ("TEST:fail", 1), ("DEBUG", 2), ("EDIT", 1), ("TEST:ok", 1), ("DONE", 1),
    ]  # fmt: skip
    assert [s.name for s in abstract(actions, "phase")][:3] == ["USER_TURN", "TEST", "DEBUG"]
    assert abstract(actions, "tail:2")[1].name == "USER_TURN:TASK_NEW > TEST:fail"
    assert coarsen("TEST:fail") == "TEST" and coarsen("USER_TURN:TASK_NEW > EDIT") == "EDIT"
    with pytest.raises(ValueError, match="tail:K"):
        check_abstraction("tail:9")
    assert outcome(case, actions) == "completed"


def test_outcomes():
    def labelled(*events):
        case = case_of([event(0, role="user", text="Fix it"), *events])
        return outcome(case, label_case(case))

    assert labelled(call(1, "pytest", "c"), result(2, "c", failed=True)) == "abandoned"
    assert labelled(event(1, text="Done."), event(2, role="user", text="No, that is wrong")) == "user_aborted"
    assert labelled(event(1, text="API Error: overloaded")) == "error"
    assert labelled(event(1, text="Done."), event(2, role="user", text="thanks")) == "completed"
    # A final summary after a failing check is not a completion.
    assert labelled(call(1, "sed -i x a.py", "a"), result(2, "a"), call(3, "pytest", "b"),
                    result(4, "b", failed=True), event(5, text="Done.")) == "abandoned"  # fmt: skip


# --- mining ------------------------------------------------------------------------------------------------------


def sequence(names, outcome="completed", case_id="c"):
    from thearc.sessions.analysis.aeg.events import Case
    from thearc.sessions.analysis.aeg.states import State

    case = Case(case_id, case_id, case_id, case_id, "main", "pi", True, False, None, None, None)
    states = []
    for name in names:
        if states and states[-1].name == name:
            states[-1].repeat += 1
        else:
            states.append(State(name, 1, []))
    return CaseSequence(case, [], states, outcome)


def test_loops_find_self_loops_and_cycles_with_exit_curves():
    looping = sequence(["EDIT", "TEST:fail", "EDIT", "TEST:fail", "EDIT", "TEST:ok", "EXPLORE", "EXPLORE", "EXPLORE"])
    instances = loop_instances(looping)
    assert {(i.cycle, i.iterations) for i in instances} == {(("EXPLORE",), 3), (("EDIT", "TEST:fail"), 2)}
    rows = loops([looping], [*instances, *instances, *instances])
    cycle = next(row for row in rows if row["kind"] == "cycle")
    assert cycle["instances"] == 3 and cycle["exit_probability"] == {2: 1.0}


def test_prefixspan_and_contrast():
    found = dict(prefixspan([["a", "b", "c"], ["a", "c"], ["b", "c"]], min_support=2))
    assert found[("a", "c")] == 2 and found[("b", "c")] == 2 and ("a", "b") not in found
    failed = [sequence(["EDIT", "TEST:fail", "DONE"], "abandoned", f"f{i}") for i in range(6)]
    passed = [sequence(["EDIT", "TEST:ok", "DONE"], "completed", f"p{i}") for i in range(6)]
    table = contrast(failed, passed)
    assert table["over"][0]["pattern"][0:2] in (["TEST:fail"], ["EDIT", "TEST:fail"], ["TEST:fail", "DONE"])
    assert all(row["log_odds"] > 0 for row in table["over"])


def test_happy_path_flags_paths_no_case_took():
    sequences = [sequence(["A", "B"], case_id="1"), sequence(["A", "C"], case_id="2"),
                 sequence(["X", "B"], case_id="3")]  # fmt: skip
    path = happy_path(sequences, aggregate(sequences))
    assert path["graph_path"][0] == "START" and path["graph_path"][-1] == "END:completed"
    assert path["variant_cases"] == 1


# --- whole pipeline, LLM labelling, CLI -----------------------------------------------------------------------------


def claude(native, day, steps, final="Fixed it."):
    records = [{"type": "user", "sessionId": native, "timestamp": f"2026-10-{day:02d}T10:00:00Z",
                "message": {"role": "user", "content": "Fix the bug"}}]  # fmt: skip
    for number, (command, failed) in enumerate(steps):
        records.append({"type": "assistant", "sessionId": native, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{number}", "name": "Bash", "input": {"command": command}}]}})  # fmt: skip
        records.append({"type": "user", "sessionId": native, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{number}", "content": "x", "is_error": failed}]}})  # fmt: skip
    if final:
        records.append({"type": "assistant", "sessionId": native,
                        "message": {"role": "assistant", "content": final}})  # fmt: skip
    return records


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "claude" / "project"
    for number in range(12):
        failing = number % 2 == 0
        steps = [("cat src/app.py", False), ("pytest -q", True), ("sed -i s/a/b/ src/app.py", False),
                 ("pytest -q", failing)]  # fmt: skip
        write_jsonl(root / f"s{number:02d}.jsonl", claude(f"s{number:02d}", 1 + number, steps))
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "claude")
        yield sessions


def threads(store):
    return [t for record in store.list() for t in store.get(record.id).root_threads]


def test_the_pipeline_builds_a_graph_and_mines_it(store, tmp_path):
    view = ExecutionGraphView.build(threads(store))
    summary = view.summary()
    assert summary["cases"] == 12 and summary["outcomes"] == {"completed": 6, "abandoned": 6}
    assert any(row["pattern"] == ["TEST:fail", "DONE"] for row in view.mined.contrast["over"])
    assert view.validation["abstraction"] == "phase"
    assert view.archetypes["clusters"] and view.archetypes["method"]
    markdown = view.to_markdown()
    assert "## Loops" in markdown and "## Failure indicators" in markdown and "## Archetypes" in markdown
    assert view.to_mermaid().startswith("flowchart LR") and view.to_sankey().startswith("sankey-beta")
    folder = export_execution_graph(view, tmp_path / "export")
    names = {path.name for path in folder.iterdir()}
    assert {"index.html", "graph.json", "report.md", "event_log.csv", "intents_sample.csv"} <= names
    assert {"events.parquet", "events.jsonl"} & names
    assert "case:concept:name" in (folder / "event_log.csv").read_text().splitlines()[0]


def test_baseline_contrast(store):
    view = ExecutionGraphView.build(threads(store)[:6], baseline=threads(store)[6:])
    assert view.mined.baseline is not None and view.baseline_cases == 6


def test_llm_labeler_batches_caches_and_falls_back(tmp_path):
    prompts = []

    def complete(prompt):
        prompts.append(prompt)
        return 'Here: {"1": "CORRECTION", "2": "nonsense"}'

    cache = tmp_path / "labels.json"
    labeler = LLMLabeler(complete, cache=cache)
    assert labeler.label("user", ["Hmm, do it differently", "go", "go"]) == {"Hmm, do it differently": "CORRECTION"}
    assert len(prompts) == 1 and json.loads(cache.read_text())
    again = LLMLabeler(lambda prompt: pytest.fail("cached labels need no call"), cache=cache)
    again.label("user", ["Hmm, do it differently"])

    def broken(prompt):
        raise RuntimeError("no model")

    failing = LLMLabeler(broken)
    assert failing.label("assistant", ["x"]) == {} and failing.failures == ["no model"]


def test_cli_graphs_the_aeg_of_piped_query_results(store):
    runner = CliRunner()
    index = str(store.path)

    def piped(query):
        return runner.invoke(main, ["sessions", "--index", index, "query", "--json", query]).output

    printed = runner.invoke(main, ["sessions", "--index", index, "graph", "aeg"],
                            input=piped("select:session count:12"))  # fmt: skip
    assert printed.exit_code == 0, printed.output
    assert printed.stdout.startswith("# Abstract Execution Graph")
    queried = runner.invoke(main, ["sessions", "--index", index, "graph", "aeg", "-o", "json", "--compare",
                                   "none"], input=piped("shell:sed select:session"))  # fmt: skip
    assert queried.exit_code == 0, queried.output
    assert json.loads(queried.stdout)["summary"]["cases"] == 12
    sankey = runner.invoke(main, ["sessions", "--index", index, "graph", "aeg", "-o", "sankey"],
                           input=piped("select:session count:3"))  # fmt: skip
    assert sankey.exit_code == 0 and sankey.stdout.startswith("sankey-beta")
    removed = runner.invoke(main, ["sessions", "--index", index, "graph", "aeg", "--against", "x"], input="")
    assert removed.exit_code != 0 and "No such option '--against'" in removed.output
