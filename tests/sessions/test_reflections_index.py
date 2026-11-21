"""Reflections in the session index: stored there (or read from journals), bound to sessions by ID or marker, with
the in-session reflection's own turns tagged, segments filled in, and coverage per question."""

import json
import subprocess

import pytest

from honed.reflection.reflector import ids_digest
from honed.sessions import Sessions

CWD = "/work/app"


def line(kind, native, n, content, **extra):
    return json.dumps({"type": kind, "sessionId": native, "cwd": CWD, "uuid": f"{native}-{n}",
                       "timestamp": f"2099-01-01T10:00:{n:02d}Z",
                       "message": {"role": "assistant" if kind == "assistant" else "user", "content": content},
                       **extra})


def bash(native, n, call, command):
    return line("assistant", native, n, [{"type": "tool_use", "id": call, "name": "Bash",
                                         "input": {"command": command}}])


def result(native, n, call, output):
    return line("user", native, n, [{"type": "tool_result", "tool_use_id": call, "content": output}])


MARK1, MARK2 = "honed-reflect:aaaa1111", "honed-reflect:bbbb2222"


def transcript(native="s1", *, second=True):
    """Work, a /reflect turn, more work, and (with ``second``) another /reflect turn and a last message."""
    lines = [
        line("user", native, 1, "Fix the retry bug"),
        bash(native, 2, "c1", "pytest -q"),
        result(native, 3, "c1", "1 failed"),
        line("assistant", native, 4, [{"type": "text", "text": "Fixed it."}]),
        bash(native, 5, "r1", "honed reflect begin --trigger tests-green"),
        result(native, 6, "r1", f"marker: {MARK1}\nQuestion: …"),
        bash(native, 7, "r2", f"honed reflect submit {MARK1} --file /tmp/r.json"),
        result(native, 8, "r2", f"Accepted reflection_1 for {MARK1}: 1 rating"),
        line("user", native, 9, "Now add a test"),
        bash(native, 10, "c2", "pytest -q"),
        result(native, 11, "c2", "2 passed"),
    ]
    if second:
        lines += [
            bash(native, 12, "r3", "honed reflect begin"),
            result(native, 13, "r3", f"marker: {MARK2}"),
            bash(native, 14, "r4", f"honed reflect submit {MARK2} -"),
            result(native, 15, "r4", f"Accepted reflection_2 for {MARK2}"),
            line("assistant", native, 16, [{"type": "text", "text": "Reflected."}]),
        ]
    return "\n".join(lines) + "\n"


def reflection(identifier, *, marker=None, session=None, prompt="p_1", asked="a_1", revision="1.0.0@rev_x",
               mode="in-session", items=(), version="1", **evidence):
    return {
        "id": identifier, "revision": revision, "at": "2099-01-01T11:00:00+00:00",
        "reflector": {"harness": "claude", "model": "m", "usage": {"cost": 0.01}},
        "evidence": {"asked": asked, **({"marker": marker} if marker else {}),
                     **({"session": session} if session else {}), **evidence},
        "attempts": 1, "notes": [], "extra": {}, "mode": mode, "trigger": "tests-green",
        "prompt": {"name": "reflect", "version": version, "hash": prompt}, "schema": "s_1",
        "items": list(items), "output": {},
    }


@pytest.fixture
def project(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "s1.jsonl").write_text(transcript())
    root = tmp_path / "app"
    root.mkdir()
    return store, root


def open_index(tmp_path, store, root):
    sessions = Sessions(tmp_path / "index.sqlite")
    sessions.add(store, harness="claude")
    sessions.add_reflections(root)
    return sessions


def rows(sessions, sql, *params):
    return [dict(row) for row in sessions.index.connection.execute(sql, params)]


def test_in_session_reflections_are_bound_by_their_markers_and_their_turns_are_not_the_sessions_work(
        tmp_path, project):
    store, root = project
    sessions = open_index(tmp_path, store, root)
    rating = {"path": "/ratings/0", "ref": "skills/retry/SKILL.md#L3", "scale": "usefulness", "rating": "harmful"}
    sessions.add_reflection(reflection("reflection_1", marker=MARK1, items=[rating]))
    sessions.add_reflection(reflection("reflection_2", marker=MARK2))
    session = sessions.get("s1")
    work = [event.id for event in session.events]
    assert len(work) == 8  # 15 events, less the two reflections' turns (begin, its output, submit, its output).
    assert not any("honed reflect" in event.text for event in session.events)

    first, second = rows(sessions, "SELECT * FROM reflections ORDER BY id")
    assert first["session_id"] == second["session_id"] == session.id
    # The first judged the session up to its turns; the second what came after them, up to its own.
    assert (first["from_event"], first["to_event"], first["events"]) == (work[0], work[3], 4)
    assert (second["from_event"], second["to_event"], second["events"]) == (work[4], work[6], 3)
    assert first["events_digest"] == ids_digest(work[:4])
    tags = rows(sessions, "SELECT tag, count(*) AS n FROM event_tags GROUP BY tag ORDER BY tag")
    assert tags == [{"tag": "reflection:reflection_1", "n": 4}, {"tag": "reflection:reflection_2", "n": 4}]
    assert rows(sessions, "SELECT target, rating FROM reflection_ratings") == [
        {"target": "skill:retry", "rating": "harmful"}]

    # What a marker resolved to is recorded in the stored reflection too.
    (kept,) = rows(sessions, "SELECT data FROM reflections WHERE id='reflection_1'")
    evidence = json.loads(kept["data"])["evidence"]
    assert evidence["session"] == session.id and evidence["to_event"] == work[3]

    # Coverage: both reach the last work but the final message, which is the tail.
    (coverage,) = rows(sessions, "SELECT * FROM reflection_coverage")
    assert (coverage["state"], coverage["tail"], coverage["covered_to"]) == ("partial", 1, work[6])


def test_a_marker_not_yet_in_any_transcript_is_resolved_on_a_later_sync(tmp_path, project):
    store, root = project
    (store / "s1.jsonl").write_text(transcript().split(bash("s1", 5, "r1", "x")[:20])[0])  # The work only.
    sessions = open_index(tmp_path, store, root)
    sessions.add_reflection(reflection("reflection_1", marker=MARK1))
    assert rows(sessions, "SELECT session_id FROM reflections") == [{"session_id": None}]
    (store / "s1.jsonl").write_text(transcript(second=False))
    sessions.sync()
    assert rows(sessions, "SELECT session_id FROM reflections")[0]["session_id"] is not None
    (coverage,) = rows(sessions, "SELECT state, tail FROM reflection_coverage")
    assert coverage == {"state": "partial", "tail": 3}  # The work after the reflection is not covered.


def test_handoff_reflections_cover_what_they_were_handed_and_journals_are_a_source(tmp_path, project):
    store, root = project
    sessions = open_index(tmp_path, store, root)
    session = sessions.get("s1")
    work = [event.id for event in session.events]
    workspace = root / ".honed/workspace"
    workspace.mkdir(parents=True)
    git = ["git", "-C", str(workspace)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    whole = reflection("reflection_h", session=session.id, mode="handoff", handoff="x", from_event=work[0],
                       to_event=work[-1], asked="a_2")
    legacy = {key: value for key, value in reflection("reflection_old", session=session.id, mode="handoff",
                                                       events=3, asked="a_3").items()
              if key not in {"mode", "trigger", "prompt", "schema", "items"}}
    (workspace / "journal.jsonl").write_text("\n".join(json.dumps({"kind": "reflected", **data})
                                                       for data in (whole, legacy)) + "\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "j"], check=True)
    sessions.add_reflections(root)
    found = {row["id"]: row for row in rows(sessions, "SELECT * FROM reflections")}
    assert found["reflection_h"]["mode"] == "handoff" and found["reflection_h"]["source_id"].startswith(
        "reflections:journal:")
    assert found["reflection_old"]["to_event"] == work[2]  # Recorded before segments: its first `events` events.
    coverage = {row["asked"]: row for row in rows(sessions, "SELECT * FROM reflection_coverage")}
    assert coverage["a_2"]["state"] == "full" and coverage["a_3"]["tail"] == len(work) - 3

    # The same reflection stored here and in a journal is one row, the stored one, and it stays.
    sessions.add_reflection(whole)
    sessions.sync()
    (row,) = rows(sessions, "SELECT source_id FROM reflections WHERE id='reflection_h'")
    assert row["source_id"] == "reflections:stored"


def test_stored_reflections_live_in_the_index_across_syncs(tmp_path, project):
    store, root = project
    sessions = open_index(tmp_path, store, root)
    sessions.add_reflection(reflection("reflection_1", marker=MARK1))
    before = rows(sessions, "SELECT id, session_id, from_event, to_event, events_digest FROM reflections")
    (store / "s1.jsonl").write_text(transcript())  # Rewritten: its events are read again…
    (store / "s1.jsonl").touch()
    sessions.sync()
    assert rows(sessions, "SELECT id, session_id, from_event, to_event, events_digest FROM reflections") == before
    sessions.close()
    again = Sessions(tmp_path / "index.sqlite")  # …and the index reopened: the reflection is still there.
    assert [row["id"] for row in rows(again, "SELECT id FROM reflections")] == ["reflection_1"]


@pytest.fixture
def indexed(tmp_path, project):
    """s1: two in-session reflections (reflect@1, revision 1.0.0) and a handoff one (reflect@2); s2: none; s3: a
    handoff reflection (reflect@2) on all of it, and an older one (reflect@1) on revision 0.9.0."""
    store, root = project
    (store / "s2.jsonl").write_text(line("user", "s2", 1, "Unrelated") + "\n")
    (store / "s3.jsonl").write_text("\n".join([line("user", "s3", 1, "Refactor"), bash("s3", 2, "c", "ruff"),
                                               result("s3", 3, "c", "ok")]) + "\n")
    sessions = open_index(tmp_path, store, root)
    s1, s3 = sessions.get("s1"), sessions.get("s3")
    harmful = {"path": "/ratings/0", "ref": "skills/retry/SKILL.md#L3", "scale": "usefulness", "rating": "harmful"}
    sessions.add_reflection(reflection("r_in1", marker=MARK1, items=[harmful]))
    sessions.add_reflection(reflection("r_in2", marker=MARK2))
    asked_v2 = {"mode": "handoff", "prompt": "p_2", "asked": "a_2", "version": "2"}
    sessions.add_reflection(reflection("r_s1_handoff", session=s1.id, **asked_v2,
                                       from_event=s1.events[0].id, to_event=s1.events[2].id))
    sessions.add_reflection(reflection("r_s3", session=s3.id, **asked_v2,
                                       from_event=s3.events[0].id, to_event=s3.events[-1].id))
    sessions.add_reflection(reflection("r_s3_old", session=s3.id, mode="handoff", revision="0.9.0@rev_y",
                                       to_event=s3.events[-1].id))
    sessions.sync()
    return sessions


def natives(sessions, query):
    return sorted(match.session.native_id for match in sessions.query(query))


def test_query_sessions_by_their_reflections(indexed):
    assert natives(indexed, "select:session -reflected:yes") == ["s2"]
    assert natives(indexed, "select:session reflected:yes") == ["s1", "s3"]
    assert natives(indexed, "select:session -prompt:reflect") == ["s2"]
    assert natives(indexed, "select:session prompt:reflect@2") == ["s1", "s3"]
    assert natives(indexed, "select:session prompt:p_2") == ["s1", "s3"]
    assert natives(indexed, "select:session mode:in-session") == ["s1"]
    assert natives(indexed, "select:session revision:0.9.0") == ["s3"]


def test_reflection_terms_side_by_side_hold_for_the_same_reflection(indexed):
    # s1 has a reflect@2 reflection and an in-session one, but no in-session reflect@2 reflection.
    assert natives(indexed, "select:session prompt:reflect@2 mode:in-session") == []
    assert natives(indexed, "select:session prompt:reflect@1 mode:in-session") == ["s1"]
    # …and negated as a group: no reflection that is both.
    assert natives(indexed, "select:session NOT (prompt:reflect@2 mode:in-session)") == ["s1", "s2", "s3"]
    assert natives(indexed, "select:session NOT (prompt:reflect@1 revision:1.0.0)") == ["s2", "s3"]
    # Rating terms hold for one rating of that reflection.
    assert natives(indexed, "select:session rated:harmful target:skill:retry") == ["s1"]
    assert natives(indexed, "select:session rated:harmful target:skill:retry prompt:reflect@2") == []
    assert natives(indexed, "select:session rated:helpful target:skill:retry") == []


def test_coverage_of_a_question(indexed):
    assert natives(indexed, "select:session prompt:reflect@2 coverage:full") == ["s3"]
    assert natives(indexed, "select:session prompt:reflect@2 coverage:partial") == ["s1"]
    assert natives(indexed, "select:session prompt:reflect@2 coverage:none") == ["s2"]
    # What /evolve asks: sessions not fully covered by reflect@2 on revision 1.0.0.
    assert natives(indexed, "select:session NOT (prompt:reflect@2 revision:1.0.0 coverage:full)") == ["s1", "s2"]


def test_select_reflections(indexed):
    found = indexed.query("select:reflection session:s1")
    assert sorted(match.reflection["id"] for match in found) == ["r_in1", "r_in2", "r_s1_handoff"]
    (match,) = indexed.query("select:reflection rated:harmful")
    assert match.type == "reflection" and match.session.native_id == "s1"
    assert match.to_dict()["data"]["data"]["items"][0]["rating"] == "harmful"
    assert [m.reflection["id"] for m in indexed.query("select:reflection mode:handoff prompt:reflect@1")] == [
        "r_s3_old"]
    assert indexed.query("select:reflection coverage:none") == []
    assert {m.reflection["id"] for m in indexed.query("select:reflection harness:claude user:Refactor")} == {
        "r_s3", "r_s3_old"}


def test_reflection_turns_are_hidden_from_text_unless_internal(indexed):
    assert indexed.query('"honed reflect" select:event') == []
    assert len(indexed.query('"honed reflect" select:event internal:yes')) == 4
    assert len(indexed.query("tag:reflection select:event")) == 8
    assert natives(indexed, "select:session shell:pytest") == ["s1"]
