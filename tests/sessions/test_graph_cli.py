"""`thearc sessions graph`: what a query selected (piped in), graphed by one of four methods."""

import json

import pytest
from click.testing import CliRunner

from thearc.cli import main

from .test_query import call, result, user
from .test_sessions import write_jsonl


@pytest.fixture
def index(tmp_path):
    root = tmp_path / "claude" / "project"
    for native, day in (("aaaa", 1), ("bbbb", 2)):
        write_jsonl(root / f"{native}.jsonl", [
            user(native, "Run the tests", f"2026-10-0{day}T10:00:00Z"),
            call(native, 1, "pytest -q"), result(native, 1, "1 failed", error=True),
            call(native, 2, "pytest -q"), result(native, 2, "1 passed"),
        ])  # fmt: skip
    path = str(tmp_path / "index.sqlite")
    assert CliRunner().invoke(main, ["sessions", "--index", path, "sync", str(tmp_path / "claude")]).exit_code == 0
    return path


def queried(index, query="select:session"):
    output = CliRunner().invoke(main, ["sessions", "--index", index, "query", "--json", query])
    assert output.exit_code == 0, output.output
    return output.output


def graph(index, kind, *args, query="select:session", ok=True):
    output = CliRunner().invoke(main, ["sessions", "--index", index, "g", kind, *args], input=queried(index, query))
    assert (output.exit_code == 0) == ok, output.output
    return output


def test_weighted_prints_markdown_by_default_and_writes_no_files(index, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = graph(index, "weighted")
    assert output.stdout.startswith("# WeightedPatternView") and "| Pattern | Cases |" in output.stdout
    assert "graphing 2" in output.stderr and not (tmp_path / "artifacts").exists()


def test_weighted_json_is_ranked_and_cut_to_top(index):
    data = json.loads(graph(index, "weighted", "-o", "json", "--top", "2").stdout)
    assert data["case_count"] == 2 and len(data["patterns"]) == 2
    assert [p["cases"] for p in data["patterns"]] == sorted((p["cases"] for p in data["patterns"]), reverse=True)
    assert graph(index, "weighted", "-o", "mermaid").stdout.lstrip().startswith(("flowchart", "graph"))


def test_export_keeps_stdout_for_the_printed_format(index, tmp_path):
    folder = tmp_path / "report"
    only = graph(index, "weighted", "--export", str(folder))
    assert "Wrote report files" in only.stdout and (folder / "index.html").exists()
    assert {f.name for f in folder.iterdir()} >= {"weighted", "graphs.sqlite", "manifest.json"}
    assert {f.name for f in (folder / "weighted").iterdir()} >= {"weighted-patterns.md", "weighted-patterns.json"}
    both = graph(index, "weighted", "--export", str(tmp_path / "again"), "-o", "json")
    assert json.loads(both.stdout)["case_count"] == 2 and "Wrote report files" in both.stderr


def test_per_session_graphs(index, tmp_path):
    printed = graph(index, "workgraph", query="select:session count:1")
    assert printed.stdout.startswith("# ")
    mermaid = graph(index, "decision-trace", "-o", "mermaid", query="select:session count:1").stdout
    assert mermaid.lstrip().startswith(("flowchart", "graph"))
    data = json.loads(graph(index, "workgraph", "-o", "json").stdout)
    assert len(data["graphs"]) == 2 and data["graphs"][0]["thread_id"] is None
    exported = graph(index, "workgraph", "--export", str(tmp_path / "wg"))
    assert "Wrote 2 workgraph graph(s)" in exported.stdout and (tmp_path / "wg" / "workgraph" / "index.html").exists()
    many = graph(index, "workgraph", "-o", "mermaid", ok=False)
    assert "-o mermaid prints one graph; the query selected 2" in many.output


def test_events_stand_for_their_threads(index):
    output = graph(index, "workgraph", "-o", "json", query="shell:pytest")
    data = json.loads(output.stdout)
    assert len(data["graphs"]) == 2 and all(g["thread_id"] for g in data["graphs"])
    assert "event match(es) stand for the 2 thread(s)" in output.stderr


def test_ids_on_stdin(index):
    sessions = [r["data"]["id"] for r in json.loads(queried(index))["results"]]
    output = CliRunner().invoke(main, ["sessions", "--index", index, "graph", "weighted", "-o", "json"],
                                input="\n".join(s[:8] for s in sessions))  # fmt: skip
    assert output.exit_code == 0, output.output
    assert json.loads(output.stdout)["case_count"] == 2


def test_each_kind_has_its_own_options(index):
    assert "No such option '--judge'" in graph(index, "weighted", "--judge", "3", ok=False).output
    assert "No such option '--png'" in graph(index, "aeg", "--png", ok=False).output
    assert "'sankey' is not one of" in graph(index, "weighted", "-o", "sankey", ok=False).output
    assert "--png needs --export DIR" in graph(index, "weighted", "--png", ok=False).output
    missing = CliRunner().invoke(main, ["sessions", "--index", index, "graph", "aeg"], input="")
    assert missing.exit_code != 0 and "No sessions selected" in missing.output
    kinds = CliRunner().invoke(main, ["sessions", "--index", index, "graph", "--help"]).output
    assert all(kind in kinds for kind in ("workgraph", "decision-trace", "weighted", "aeg"))
