import json
import re
from datetime import UTC, datetime

import pytest
from click.testing import CliRunner

from honed.cli import main
from honed.sessions import Sessions
from honed.sessions.query import And, Not, Or, QuerySyntaxError, Term, execute, parse

from .test_sessions import write_jsonl


def test_parses_sourcegraph_style_terms_and_modifiers():
    query = parse("shell.contains:graphify harness:codex select:thread at:root")
    assert query.select == "thread" and query.pattern_type == "keyword" and not query.case_sensitive
    assert query.node == And(
        (
            Term("shell", "contains", "graphify", False, 0),
            Term("harness", "is", "codex", False, 24),
            Term("at", "is", "root", False, 52),
        )
    )


def test_boolean_structure_negation_and_precedence():
    node = parse("a or b c -tool:Bash not (d or e)").node
    assert isinstance(node, Or) and node.items[0] == Term("content", "contains", "a", False, 0)
    second = node.items[1]
    assert isinstance(second, And) and len(second.items) == 4
    assert second.items[2] == Not(Term("tool", "is", "Bash", False, 9))
    assert isinstance(second.items[3], Not) and isinstance(second.items[3].item, Or)


def test_pattern_types():
    keyword = parse('foo.bar "exact phrase" /ba+r/').node
    assert [(t.value, t.regex) for t in keyword.items] == [("foo.bar", False), ("exact phrase", False), ("ba+r", True)]
    regexp = parse('patterntype:regexp foo.bar "a b+" shell:x\\d').node
    assert [(t.value, t.regex) for t in regexp.items] == [("foo.bar", True), ("a b+", True), ("x\\d", True)]
    # Attribute fields stay exact in regexp mode unless asked to match.
    assert parse("patterntype:regexp tool:Bash").node.regex is False
    assert parse("tool.matches:^Ba").node.regex is True


def test_values_keep_balanced_parentheses_and_unknown_fields_are_text():
    assert parse("(shell:f(x) or y)").node.items[0].value == "f(x)"
    assert parse("localhost:8000").node == Term("content", "contains", "localhost:8000", False, 0)
    assert parse("/a b/ cwd:/Users/me x").node.items[0] == Term("content", "contains", "a b", True, 0)
    assert parse("/a b/ cwd:/Users/me x").node.items[1].value == "/Users/me"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("or a", "Expected a term"),
        ('"open', "Unterminated quoted string"),
        ("/a(/", "Invalid regular expression"),
        ("select:file", "Invalid select"),
        ("select:thread select:session", "more than once"),
        ("(a select:thread)", "top level"),
        ("shell.near:x", "Unknown operator"),
        ("tool:", "needs a value"),
    ],
)
def test_reports_syntax_errors_with_position(text, message):
    with pytest.raises(QuerySyntaxError, match=re.escape(message)) as error:
        parse(text)
    assert "^" in str(error.value)


def call(native, number, command, **extra):
    return {
        "type": "assistant",
        "sessionId": native,
        "timestamp": f"2026-10-0{number}T10:00:0{number}Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"c{number}", "name": "Bash", "input": {"command": command}}],
        },
        **extra,
    }


def result(native, number, text, error=False):
    return {
        "type": "user",
        "sessionId": native,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"c{number}", "content": text, "is_error": error}],
        },
    }


def user(native, text, when):
    return {"type": "user", "sessionId": native, "timestamp": when, "message": {"role": "user", "content": text}}


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "claude" / "project"
    write_jsonl(
        root / "aaaa.jsonl",
        [
            user("aaaa", "Map the router with graphify", "2026-10-01T10:00:00Z"),
            call("aaaa", 1, "graphify query 'APIRouter routes'"),
            result("aaaa", 1, "found 3 nodes"),
        ],
    )
    write_jsonl(
        root / "aaaa" / "subagents" / "agent-1.jsonl",
        [call("aaaa", 2, "pytest -x tests"), result("aaaa", 2, "SyntaxError: bad input", error=True)],
    )
    write_jsonl(
        root / "bbbb.jsonl",
        [
            user("bbbb", "Run the tests, not graphify", "2026-10-05T10:00:00Z"),
            call("bbbb", 5, "pytest -q"),
            result("bbbb", 5, "2 passed"),
        ],
    )
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "claude")
        yield sessions


def sessions_of(matches):
    return sorted(match.session.native_id for match in matches)


def test_selects_threads_by_shell_command_and_position(store):
    roots = execute(store.index, "shell.contains:graphify harness:claude select:thread at:root")
    assert [m.thread.parent_run_id for m in roots] == [None] and sessions_of(roots) == ["aaaa"]
    children = execute(store.index, "shell:pytest select:thread at:child")
    assert len(children) == 1 and children[0].thread.parent_run_id == roots[0].thread.id
    # Thread terms need some event each; negation needs none.
    assert sessions_of(execute(store.index, "shell:pytest -shell:-x select:thread")) == ["bbbb"]
    assert execute(store.index, "shell:graphify harness:codex select:thread") == []


def test_event_terms_must_hold_for_one_event(store):
    hits = execute(store.index, "shell:pytest shell:-q")
    assert [m.event.text for m in hits] == ['{"command": "pytest -q"}']
    assert execute(store.index, "shell:graphify shell:pytest") == []
    # A session matches when each term is met by some event in it.
    assert sessions_of(execute(store.index, "shell:graphify shell:pytest select:session")) == ["aaaa"]


def test_keyword_and_regexp_patterns(store):
    assert sessions_of(execute(store.index, 'GRAPHIFY "the router" select:session')) == ["aaaa"]
    assert execute(store.index, "case:yes GRAPHIFY") == []
    regexp = execute(store.index, r"patterntype:regexp result:\d+\s+passed")
    assert [m.event.text for m in regexp] == ["2 passed"]
    assert [m.event.text for m in execute(store.index, r"result:/^found \d/")] == ["found 3 nodes"]
    # In keyword mode regex metacharacters are literal text.
    assert execute(store.index, r"result:\d+") == []


def test_attribute_scope_and_date_fields(store):
    failures = execute(store.index, "action:shell status:failure kind:tool_result")
    assert [m.event.text for m in failures] == ["SyntaxError: bad input"] and failures[0].event.tool_name == "Bash"
    assert len(execute(store.index, "tool:bash kind:tool_call")) == 3
    assert sessions_of(execute(store.index, "after:2026-10-03 select:session")) == ["bbbb"]
    now = datetime(2026, 10, 6, tzinfo=UTC)
    assert sessions_of(execute(store.index, "after:2d select:session", now=now)) == ["bbbb"]
    assert sessions_of(execute(store.index, "session:bbb select:session")) == ["bbbb"]
    assert sessions_of(execute(store.index, "-session:bbb select:session")) == ["aaaa"]


def test_count_limit_and_internal(store):
    assert len(execute(store.index, "kind:tool_call count:2")) == 2
    assert len(execute(store.index, "kind:tool_call count:2", limit=3)) == 3
    # Bare patterns skip system prompts and metadata records unless internal:yes.
    assert len(execute(store.index, "pytest")) == 2
    assert len(execute(store.index, "pytest internal:yes")) >= 2


def test_cli_query(tmp_path, store):
    runner = CliRunner()
    index = str(store.path)
    output = runner.invoke(
        main, ["sessions", "--index", index, "query", "-harness:codex", "shell:graphify", "select:thread"]
    )
    assert output.exit_code == 0, output.output
    assert len(output.output.splitlines()) == 1 and "root" in output.output
    error = runner.invoke(main, ["sessions", "--index", index, "q", "select:file"])
    assert error.exit_code != 0 and "Invalid select" in error.output
    assert "patterntype" in runner.invoke(main, ["sessions", "--index", index, "q", "--fields"]).output


def test_cli_explains_empty_results(tmp_path, store):
    runner = CliRunner()
    none = runner.invoke(main, ["sessions", "--index", str(store.path), "q", "shell:nothing-like-this"])
    assert none.exit_code == 0 and "No matches." in none.output
    empty = runner.invoke(main, ["sessions", "--index", str(tmp_path / "empty.sqlite"), "q", "select:thread"])
    assert empty.exit_code == 0 and "No sessions are indexed" in empty.output


@pytest.mark.parametrize(
    ("query", "kind", "key"),
    [
        ("shell:graphify", "event", "text"),
        ("shell:graphify select:thread", "thread", "parent_run_id"),
        ("shell:graphify select:session", "session", "native_id"),
    ],
)
def test_cli_json_wraps_typed_results(store, query, kind, key):
    output = CliRunner().invoke(main, ["sessions", "--index", str(store.path), "q", "--json", query])
    assert output.exit_code == 0, output.output
    results = json.loads(output.output)["results"]
    assert len(results) == 1 and results[0]["type"] == kind and key in results[0]["data"]
    assert "raw" not in results[0]["data"] and "raw_pointer" not in results[0]["data"]


def test_cli_json_without_matches_is_an_empty_list(store):
    output = CliRunner().invoke(main, ["sessions", "--index", str(store.path), "q", "--json", "zzzz"])
    assert json.loads(output.stdout) == {"results": []}


def test_shell_matches_the_command_not_its_json(store):
    assert [m.event.arguments for m in execute(store.index, "shell.startswith:pytest")] == [
        {"command": "pytest -q"},
        {"command": "pytest -x tests"},
    ]
    assert execute(store.index, "shell.startswith:tests") == []
    assert len(execute(store.index, "patterntype:regexp shell:^(pytest|graphify)")) == 3
    assert execute(store.index, 'shell.startswith:"{"') == []


def test_a_call_has_its_results_status(store):
    failed = execute(store.index, "shell.startswith:pytest status:failure")
    assert [m.event.arguments["command"] for m in failed] == ["pytest -x tests"]
    assert failed[0].event.kind == "tool_call"
    assert sessions_of(execute(store.index, "shell:pytest -status:failure select:thread")) == ["bbbb"]


def test_shell_reads_commands_inside_exec_scripts(tmp_path):
    script = 'const r = await tools.exec_command({cmd: "graphify query routes", workdir: "/w"});'
    exec_call = call("cccc", 1, "")
    exec_call["message"]["content"][0].update(name="exec", input={"code": script})
    write_jsonl(tmp_path / "claude" / "p" / "cccc.jsonl", [user("cccc", "go", "2026-10-01T10:00:00Z"), exec_call])
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "claude")
        (match,) = sessions.query("shell.startswith:graphify")
        assert match.event.tool_name == "exec"


def test_sessions_query_reads_a_snapshot(store):
    assert [m.type for m in store.query("shell:graphify select:session")] == ["session"]
    assert len(store.query("kind:tool_call", limit=1)) == 1
    with pytest.raises(QuerySyntaxError):
        store.query("select:file")


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("def fun(", ["def", "fun("]),
        ("a (b", ["a", "(b"]),
        ("a b)", ["a", "b)"]),
        ("()", ["()"]),
        ("(a or b) c)", ["(a", "b)", "c)"]),  # One stray parenthesis makes all of them text.
    ],
)
def test_parentheses_that_do_not_pair_up_are_text(text, values):
    node = parse(text).node
    terms = [node] if isinstance(node, Term) else [t for t in node.items if isinstance(t, Term)]
    assert [t.value for t in terms] == values or isinstance(node, Or)


def test_sourcegraph_operators():
    node = parse("conf.Get( and log15.Error( and after").node
    assert node == And(
        (
            Term("content", "contains", "conf.Get(", False, 0),
            Term("content", "contains", "log15.Error(", False, 14),
            Term("content", "contains", "after", False, 31),
        )
    )
    upper = parse("conf.Get( AND log15.Error( OR NOT after").node
    assert isinstance(upper, Or) and isinstance(upper.items[0], And) and isinstance(upper.items[1], Not)
    assert parse('"and" OR "or"').node == Or((Term("content", "contains", "and", False, 0),
                                             Term("content", "contains", "or", False, 9)))  # fmt: skip


def test_pattern_types_are_keyword_regexp_and_lexical():
    assert parse("patterntype:regex a").pattern_type == "regexp"
    for removed in ("standard", "literal", "fulltext"):
        with pytest.raises(QuerySyntaxError, match="keyword, regexp, lexical"):
            parse(f"patterntype:{removed} a")


def test_operators_select_events(store):
    assert len(execute(store.index, "graphify AND query")) == 1
    assert len(execute(store.index, "graphify and router")) == 2  # The prompt, and the call: "APIRouter".
    assert len(execute(store.index, "router OR passed")) == 3
    assert len(execute(store.index, '"graphify query"')) == 1  # Quotes: the exact phrase.
    assert execute(store.index, '"query graphify"') == []


def test_lexical_search_uses_the_word_index_and_ranks(store):
    hits = execute(store.index, "patterntype:lexical tests graphify")
    assert [m.session.native_id for m in hits] == ["bbbb"]  # "Run the tests, not graphify": both words.
    assert execute(store.index, "patterntype:lexical graph") == []  # Words, not substrings.
    assert len(execute(store.index, "patterntype:lexical graphify")) == 3
    with pytest.raises(QuerySyntaxError, match="ignores case"):
        parse("patterntype:lexical case:yes x")


def test_events_carry_match_ranges(store):
    (hit,) = execute(store.index, "user:Map router")
    assert [hit.event.text[a:b] for a, b in hit.match_ranges] == ["Map", "router"]
    (hit,) = execute(store.index, r"patterntype:regexp result:\d+\s+passed")
    assert hit.match_ranges == ((0, 8),)
    assert execute(store.index, "not graphify kind:tool_result count:1")[0].match_ranges == ()


def test_results_reach_their_session_and_trajectory(store):
    (hit,) = store.query("result:found")
    assert hit.history.metadata.native_id == "aaaa"
    assert hit.trajectory is not None and hit.trajectory.prompt_text == "Map the router with graphify"
    assert execute(store.index, "result:found")[0].loader is None


def test_queries_within_a_session_or_trajectory(store):
    session = store.get("bbbb")
    assert [m.event.text for m in session.query("result:passed")] == ["2 passed"]
    assert session.query("graphify -kind:message") == []  # Another session's call is out of scope.
    assert [m.session.native_id for m in session.query("select:thread")] == ["bbbb"]
    (trajectory,) = session.trajectories
    assert len(trajectory.query("kind:tool_call")) == 1 and trajectory.query("kind:tool_call")[0].trajectory


def test_cwd_expands_the_home_folder(tmp_path, monkeypatch):
    record = user("dddd", "Fix it", "2026-10-01T10:00:00Z") | {"cwd": str(tmp_path / "work" / "app")}
    write_jsonl(tmp_path / "claude" / "p" / "dddd.jsonl", [record])
    monkeypatch.setenv("HOME", str(tmp_path))
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(tmp_path / "claude")
        assert [m.session.native_id for m in sessions.query("cwd:~/work select:session")] == ["dddd"]
        assert sessions.query("cwd:~/elsewhere select:session") == []


def test_help_explains_the_syntax_with_examples():
    output = CliRunner().invoke(main, ["sessions", "query", "--help"]).output
    for part in ("Terms and operators", "Pattern types", "Filters", "Modifiers", "Examples", "hnd s q", "a OR b", "lexical"):
        assert part in output


def test_family_selects_a_thread_tree_from_any_of_its_members(store):
    root = execute(store.index, "session:aaaa select:thread at:root")[0].thread
    child = execute(store.index, "session:aaaa select:thread at:child")[0].thread
    for reference in ("aaaa", root.id[:10], child.id):  # A native ID, a run prefix, the child's run.
        family = execute(store.index, f"family:{reference} select:thread")
        assert {m.thread.id for m in family} == {root.id, child.id}, reference
    assert [m.thread.id for m in execute(store.index, "family:aaaa at:child select:thread")] == [child.id]
    assert sessions_of(execute(store.index, "family:aaaa shell:pytest")) == ["aaaa"]  # Events of the family.
    assert execute(store.index, "family:nothing select:thread") == []


def test_family_follows_parent_links_across_sessions(tmp_path):
    """Codex records a subagent as a session of its own, linked to its parent's thread."""
    root = tmp_path / "codex"
    for name, meta, text in (("parent", {"id": "parent"}, "parent phrase"),
                             ("child", {"id": "child", "source": {"subagent": {"thread_spawn": {
                                 "parent_thread_id": "parent", "depth": 1}}}}, "child phrase")):  # fmt: skip
        write_jsonl(root / f"rollout-{name}.jsonl", [
            {"type": "session_meta", "payload": meta},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": text}]}},
        ])  # fmt: skip
    with Sessions(tmp_path / "index.sqlite") as sessions:
        sessions.add(root)
        for reference in ("parent", "child"):
            family = execute(sessions.index, f"family:{reference} select:thread")
            assert sorted(m.session.native_id for m in family) == ["child", "parent"], reference
        assert sorted(m.session.native_id for m in execute(sessions.index, "family:child select:session")) == [
            "child", "parent"]
