"""Graph artifacts: every kind stored in one model in the export directory, and queried there."""

import json

import pytest
from click.testing import CliRunner

from thearc.cli import main
from thearc.sessions.analysis import graphquery, graphstore
from thearc.sessions.query import QuerySyntaxError

from . import test_aeg

store = test_aeg.store  # 12 alike sessions: cat, pytest (fails), sed, pytest (fails in every other one).


@pytest.fixture
def artifact(store, tmp_path):
    """Every kind exported into one directory."""
    runner = CliRunner()
    index = str(store.path)
    selected = runner.invoke(main, ["sessions", "--index", index, "query", "--json", "select:session"]).output
    folder = tmp_path / "graphs"
    for kind in ("aeg", "weighted", "workgraph", "decision-trace"):
        extra = ["--compare", "none"] if kind == "aeg" else []
        result = runner.invoke(main, ["sessions", "--index", index, "graph", kind, *extra, "--export", str(folder)],
                               input=selected)  # fmt: skip
        assert result.exit_code == 0, result.output
    return folder


def run(folder, text, **options):
    return graphquery.execute([folder], text, **options)


def test_one_directory_holds_every_kind(artifact):
    assert {p.name for p in artifact.iterdir()} >= {"graphs.sqlite", "manifest.json", "index.html", "aeg",
                                                     "weighted", "workgraph", "decision-trace"}  # fmt: skip
    manifest = json.loads((artifact / "manifest.json").read_text())
    assert set(manifest["exports"]) == {"aeg", "weighted", "workgraph", "decision-trace"}
    assert len(manifest["exports"]["aeg"]["input"]["sessions"]) == 12
    kinds = {m.data["kind"]: m for m in run(artifact, "select:graph count:100")}
    assert set(kinds) == {"aeg", "weighted", "workgraph", "decision-trace"}
    assert kinds["aeg"].data["walks"] == 12


def test_exporting_a_kind_again_replaces_only_it(artifact, store):
    before = len(run(artifact, "graph:workgraph select:graph count:100"))
    selected = CliRunner().invoke(main, ["sessions", "--index", str(store.path), "query", "--json",
                                         "select:session count:3"]).output  # fmt: skip
    CliRunner().invoke(main, ["sessions", "--index", str(store.path), "graph", "workgraph", "--export",
                              str(artifact)], input=selected)  # fmt: skip
    assert before == 12 and len(run(artifact, "graph:workgraph select:graph count:100")) == 3
    assert len(run(artifact, "graph:aeg select:walk count:100")) == 12


def test_nodes_edges_and_numbers(artifact):
    states = {m.data["label"] for m in run(artifact, "graph:aeg label.startswith:TEST")}
    assert states == {"TEST:fail", "TEST:ok"}
    (failing,) = run(artifact, "graph:aeg label.is:TEST:fail")
    assert failing.data["occurrences"] == 18  # 12 cases fail first, 6 again at the end.
    edges = run(artifact, 'graph:aeg edge:"EDIT -> TEST:*" select:edge')
    assert {m.data["target"] for m in edges} == {"TEST:fail", "TEST:ok"}
    rare = run(artifact, "graph:aeg edge.count<=6 edge:\"EDIT -> *\" select:edge")
    assert {m.data["target"] for m in rare} == {"TEST:ok", "TEST:fail"}
    assert run(artifact, "graph:aeg node.visits>100") == []


def test_walks_paths_loops_and_outcomes(artifact):
    failed = run(artifact, 'path:"EDIT -> TEST:fail -> DONE" select:walk')
    assert len(failed) == 6 and all(m.data["outcome"] == "abandoned" for m in failed)
    assert failed[0].data["match"] and failed[0].data["steps"][failed[0].data["match"][0]] == "EDIT"
    assert len(run(artifact, 'path:"^START -> USER_TURN* ->> TEST:ok ->> $" select:walk')) == 6
    assert len(run(artifact, 'graph:aeg path:"TEST:ok ->> END*$" select:walk')) == 6
    assert len(run(artifact, "graph:aeg outcome:failed select:walk")) == 6
    assert len(run(artifact, "graph:aeg -outcome:failed select:walk")) == 6
    assert len(run(artifact, 'graph:aeg path:"!EDIT -> DONE" select:walk')) == 12
    assert run(artifact, 'graph:aeg loop:"TEST:fail EDIT" iterations>=2 select:walk') == []


def test_annotations_sessions_and_events(artifact):
    findings = run(artifact, "finding:premature_done select:annotation")
    assert len(findings) == 6 and findings[0].data["step"] is not None
    sessions = run(artifact, "finding:premature_done select:session")
    assert len(sessions) == 6
    walks = run(artifact, "critical:check_failed select:walk")
    assert len(walks) == 6
    # Joined across kinds through shared events: WorkGraph nodes of sessions the AEG flagged.
    events = run(artifact, "aeg.finding:premature_done workgraph.kind:tool_call select:event count:1000")
    assert events and all(m.data["session_id"] in {s.data["id"] for s in sessions} for m in events)


def test_kind_prefix_and_bare_words(artifact):
    assert {m.data["graph"] for m in run(artifact, "weighted.label:action select:node")} == {"weighted"}
    assert run(artifact, "pytest graph:aeg")  # A bare word: label or text (AEG samples hold commands).
    assert run(artifact, 'reaches:"request ~> action*" graph:decision-trace select:graph')


def test_syntax_errors_and_hints(artifact):
    with pytest.raises(QuerySyntaxError, match="select"):
        graphquery.parse("select:nope")
    with pytest.raises(QuerySyntaxError, match="iterations needs a loop"):
        graphquery.parse("iterations>=3")
    with pytest.raises(QuerySyntaxError, match="A -> B"):
        graphquery.parse("edge:EDIT")
    hints = graphquery.check_labels([artifact], graphquery.parse("label.is:TEST:failed"))
    assert hints and "TEST:fail" in hints[0]


def test_cli_query_info_json_and_piping(artifact, store):
    runner = CliRunner()
    index = str(store.path)
    info = runner.invoke(main, ["sessions", "--index", index, "graph", "q", str(artifact), "--info"])
    assert info.exit_code == 0 and "aeg" in info.output and "workgraph" in info.output
    printed = runner.invoke(main, ["sessions", "--index", index, "g", "q", str(artifact),
                                   'path:"EDIT -> TEST:fail" select:walk'])  # fmt: skip
    assert printed.exit_code == 0 and "[EDIT]" in printed.output
    piped = runner.invoke(main, ["sessions", "--index", index, "g", "q", "--json", str(artifact.parent),
                                 "finding:premature_done select:thread"])  # fmt: skip
    assert piped.exit_code == 0, piped.output
    results = json.loads(piped.stdout)["results"]
    assert len(results) == 6 and results[0]["type"] == "thread"
    graphed = runner.invoke(main, ["sessions", "--index", index, "g", "weighted", "-o", "json"], input=piped.stdout)
    assert graphed.exit_code == 0 and json.loads(graphed.stdout)["case_count"] == 6
    missing = runner.invoke(main, ["sessions", "--index", index, "g", "q", "label:x"])
    assert missing.exit_code != 0 and "artifact directory first" in missing.output
    fields = runner.invoke(main, ["sessions", "--index", index, "g", "q", "--fields"])
    assert "path" in fields.output and "select" in fields.output


def test_stale_inputs_are_reported(artifact, store):
    manifest = json.loads((artifact / "manifest.json").read_text())
    session = next(iter(manifest["exports"]["aeg"]["input"]["sessions"]))
    manifest["exports"]["aeg"]["input"]["sessions"][session] += 1  # As if the session went on since.
    (artifact / "manifest.json").write_text(json.dumps(manifest))
    assert graphstore.stale(artifact, store.path) == [
        f"aeg: 1 session(s) changed or gone since {manifest['exports']['aeg']['built_at']}"]
