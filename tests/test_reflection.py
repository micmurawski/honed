"""Reflection without model calls: a scripted runtime calls the real submit tool; CLI runtimes are faked."""

import json
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import BaseModel

from honed.reflection import Conversation, Reflection, ReflectionError, Reflector, Runtime, Submission, ToolEndpoint
from honed.reflection import runtimes as runtime_module
from honed.reflection.prompts import prompt_hash, schema_hash
from honed.reflection.reflector import events_digest, fingerprint
from honed.resources import CategoricalScale, ReflectionSchema, ResourceTarget
from honed.sessions import InstalledSession

from .sessions.test_round_trip import SESSION

usefulness = CategoricalScale("usefulness", ["harmful", "neutral", "helpful"])
LIMITS = ResourceTarget(kind="skill", name="retry", section="Retry > Limits")


class Insight(BaseModel):
    insight: str


SCHEMA = ReflectionSchema(usefulness, Insight)
TOOL = ToolEndpoint("http://127.0.0.1:1/x/mcp")


def call_tool(url: str, items: list[dict] | dict) -> dict:
    """One MCP tools/call, as a harness would make it: a list goes in ``items``, an object in ``reflection``."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "submit_reflection",
            "arguments": {"items" if isinstance(items, list) else "reflection": items},
        },
    }
    request = urllib.request.Request(
        url,
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())["result"]


class Scripted(Runtime):
    """Each turn submits prepared item lists through the real tool, and records what it was sent and told."""

    def __init__(self, *turns: list[list[dict]], harness: str = "codex", can_fork: bool = True, usage=None):
        super().__init__(model="scripted")
        self.harness, self.can_fork, self.answers, self.usage = harness, can_fork, list(turns), usage
        self.sent: list[str] = []
        self.results: list[dict] = []
        self.workspace: Path | None = None
        self.fork: InstalledSession | None = None
        self.files: list[str] = []

    @contextmanager
    def conversation(self, workspace, tool, *, fork=None):
        self.workspace, self.fork = workspace, fork
        self.files = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file())
        self.texts = {path: (workspace / path).read_text() for path in self.files}
        runtime = self

        class Talk(Conversation):
            id = "reflector-session-1"

            def send(self, message):
                runtime.sent.append(message)
                for items in runtime.answers.pop(0):
                    runtime.results.append(call_tool(tool.url, items))
                if runtime.usage:
                    self.usage += runtime.usage
                return "done"

        yield Talk()


def answer(*items: dict) -> list[list[dict]]:
    """A turn that submits ``items`` once."""
    return [list(items)]


GOOD = {"ref": "skills/retry/SKILL.md#L17", "rating": "harmful", "insight": "The limit looped."}


def test_frozen_lines_are_not_rated(agent):
    frozen = agent.model_copy(update={"frozen": ["skill:retry"]})
    rules = {"ref": "rules/style.md#L3", "rating": "helpful", "insight": "Plain text."}
    runtime = Scripted([[GOOD, rules], [rules]])
    reflection = Reflector(runtime=runtime).reflect("Review.", frozen, SCHEMA)
    assert runtime.results[0]["isError"] and "frozen" in runtime.results[0]["content"][0]["text"]
    assert [str(item.ref) for item in reflection] == ["rules/style.md#L3"]
    assert "`skill:retry`" in runtime.sent[0]
    rated = Reflector(runtime=Scripted(answer(GOOD, rules))).reflect("Review.", agent, SCHEMA)
    assert [entry.target.kind for entry in frozen.add_reflection(rated)] == ["rule"]  # Frozen since: skipped.


def test_reflect_returns_validated_items_and_reads_files_from_its_own_folder(agent):
    runtime = Scripted(answer(GOOD))
    reflection = Reflector(runtime=runtime).reflect("Review the retry skill.", agent, SCHEMA, evidence="e1: failed")
    assert isinstance(reflection, Reflection) and len(reflection) == 1 and reflection.attempts == 1
    assert reflection[0].rating == "harmful" and str(reflection[0].ref) == "skills/retry/SKILL.md#L17"
    assert reflection.revision == agent.revision and reflection.reflector["session"] == "reflector-session-1"
    assert "skills/retry/SKILL.md" in runtime.files and not any("journal" in f for f in runtime.files)
    (first,) = runtime.sent
    assert "Review the retry skill." in first and "## Evidence\n\ne1: failed" in first
    assert str(runtime.workspace) in first and "submit_reflection" in first
    assert runtime.results[0]["isError"] is False


def test_the_tool_rejects_invalid_items_until_the_reflector_fixes_them(agent):
    bad = [
        {"ref": "skills/retry/SKILL.md#L99", "rating": "harmful", "insight": "x"},
        {"ref": "skills/retry/SKILL.md#L17", "rating": "awful", "insight": "x"},
    ]
    fixed_rating = [bad[0], GOOD]
    runtime = Scripted([bad, fixed_rating, [GOOD]])  # One turn: two rejected calls, then an accepted one.
    reflection = Reflector(runtime=runtime).reflect("Review.", agent, SCHEMA)
    assert reflection.attempts == 3 and len(reflection) == 1 and len(runtime.sent) == 1
    shape, lines = (result["content"][0]["text"] for result in runtime.results[:2])
    assert runtime.results[0]["isError"] and "/1/rating:" in shape and "'awful' is not one of" in shape
    assert "complete reflection" in shape and "/0/ref:" in lines and "has 17 lines" in lines  # Refs: once valid.


def test_reflection_fails_without_an_accepted_submission(agent):
    runtime = Scripted([[{"ref": "nope", "rating": "harmful"}]])
    with pytest.raises(ReflectionError, match="1 rejected") as error:
        Reflector(runtime=runtime).reflect("Review.", agent, SCHEMA)
    assert error.value.code == "no_submission"


def test_the_tool_declares_the_item_schema(agent):
    with Submission(agent.files(), SCHEMA).serve() as tool:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        request = urllib.request.Request(
            tool.url,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(request) as response:
            (declared,) = json.loads(response.read())["result"]["tools"]
    items = declared["inputSchema"]["properties"]["items"]
    assert declared["name"] == "submit_reflection" and items["items"]["required"][:2] == ["ref", "rating"]
    assert tool.qualified == ("mcp__honed__submit_reflection",)


def test_an_empty_list_is_a_valid_reflection(agent):
    assert len(Reflector(runtime=Scripted(answer())).reflect("Review.", agent, SCHEMA)) == 0


def test_add_reflection_records_each_item_per_target_once(agent):
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", agent, SCHEMA, run="r7")
    (entry,) = agent.add_reflection(reflection)
    assert agent.journal.ratings() == [entry] and entry.target == LIMITS and entry.at == reflection.at
    assert entry.metadata == {"insight": "The limit looped."} and entry.item_id == f"{reflection.id}#/0"
    assert entry.extra == {"reflection": reflection.id, "reflector": "codex", "model": "scripted", "run": "r7"}
    assert agent.journal.summaries(usefulness)[LIMITS].counts["harmful"] == 1
    assert agent.add_reflection(reflection) == () and len(agent.journal.ratings()) == 1  # Same IDs: nothing new.
    replayed = Reflection.from_dict(json.loads(json.dumps(reflection.to_dict())), SCHEMA)
    assert replayed == reflection and agent.add_reflection(replayed) == ()


def test_an_item_spanning_two_sections_rates_both(agent):
    both = {**GOOD, "ref": "skills/retry/SKILL.md#L13-L17"}
    reflection = Reflector(runtime=Scripted(answer(both))).reflect("Review.", agent, SCHEMA)
    entries = agent.add_reflection(reflection)
    assert sorted(entry.target.section for entry in entries) == ["Retry > Examples", "Retry > Limits"]
    assert len({entry.id for entry in entries}) == 2 and {entry.item_id for entry in entries} == {f"{reflection.id}#/0"}


def test_add_reflection_refuses_another_revision(agent):
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", agent, SCHEMA)
    edited = agent.model_copy(update={"version": "2"})
    with pytest.raises(ValueError, match="judged 1@rev_"):
        edited.add_reflection(reflection)


def test_handoff_installs_the_history_and_forks_it(agent, tmp_path):
    runtime = Scripted(answer(GOOD))
    trajectory = SESSION.trajectories[0]
    reflection = Reflector(runtime=runtime, home=tmp_path).handoff("Review.", trajectory, agent, SCHEMA)
    assert runtime.fork is not None and runtime.fork.harness == "codex"
    assert not runtime.fork.path.exists()  # Removed: left in the store, it would count as a session of the project.
    assert reflection.evidence["handoff"] == runtime.fork.session_id
    assert reflection.evidence["events"] == len(trajectory.selected_events)
    assert reflection.evidence["digest"] == events_digest(trajectory) != events_digest(SESSION)  # What it was handed.
    assert reflection.evidence["asked"] == fingerprint("Review.", SCHEMA) != fingerprint("Review again.", SCHEMA)
    assert any("thinking" in note for note in reflection.notes)  # Best-effort export of foreign thinking.
    assert "## Evidence" not in runtime.sent[0]  # The forked history is the evidence.
    again = Reflector(runtime=Scripted(answer(GOOD)), home=tmp_path).handoff("Review.", trajectory, agent, SCHEMA)
    assert again.evidence["handoff"] == reflection.evidence["handoff"]  # Same selection, same installed copy.


def test_handoff_tells_the_reflector_what_the_record_leaves_out(agent, tmp_path, monkeypatch):
    from honed.reflection import reflector

    plain = Scripted(answer(GOOD))
    Reflector(runtime=plain, home=tmp_path).handoff("Review.", SESSION, agent, SCHEMA)
    assert "How the session ran" not in plain.sent[0]

    monkeypatch.setitem(reflector.UNRECORDED, SESSION.metadata.harness, "This record has no system prompt.")
    runtime = Scripted(answer(GOOD))
    reflection = Reflector(runtime=runtime, home=tmp_path).handoff(
        "Review.", SESSION, agent, SCHEMA, provenance="demo 1 was installed for this session.")
    assert "## How the session ran (not in its record)\n\nThis record has no system prompt.\n\n" \
           "demo 1 was installed for this session." in runtime.sent[0]
    assert reflection.evidence["provenance"] == "demo 1 was installed for this session."
    assert "provenance" not in reflection.extra


def test_harnesses_that_cannot_fork_refuse_handoff(agent):
    reflector = Reflector(runtime=Scripted(harness="antigravity", can_fork=False))
    with pytest.raises(ReflectionError, match="use reflect"):
        reflector.handoff("Review.", SESSION, agent, SCHEMA)


@pytest.fixture
def commands(monkeypatch):
    """Fake CLI runs: record each command and answer with the next prepared stdout."""
    calls: list[dict] = []
    outputs: list[str] = []

    def run(command, *, cwd, timeout, stdin=None):
        calls.append({"command": command, "cwd": cwd, "stdin": stdin})
        return outputs.pop(0)

    monkeypatch.setattr(runtime_module, "_run", run)
    return calls, outputs


def test_claude_runtime_resumes_its_session_between_turns(commands, tmp_path):
    calls, outputs = commands
    outputs += [
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "first", "session_id": "s-1"}),
        json.dumps(
            {"type": "result", "subtype": "success", "is_error": False, "result": "second", "session_id": "s-1"}
        ),
    ]
    with runtime_module.ClaudeRuntime(model="sonnet").conversation(tmp_path, TOOL) as talk:
        assert talk.send("hello") == "first" and talk.send("again") == "second"
    first, second = (call["command"] for call in calls)
    assert "--resume" not in first and first[first.index("--tools") + 1] == "Read,Grep,Glob"
    assert second[second.index("--resume") + 1] == "s-1" and calls[0]["stdin"] == "hello"


def test_claude_fork_runs_where_the_session_was_recorded(commands, tmp_path):
    calls, outputs = commands
    project = tmp_path / "project"
    project.mkdir()
    transcript = tmp_path / "copy.jsonl"
    transcript.write_text(json.dumps({"type": "user", "cwd": str(project)}) + "\n")
    outputs.append(json.dumps({"subtype": "success", "is_error": False, "result": "ok", "session_id": "new"}))
    installed = InstalledSession("claude", "copy-id", transcript, "claude --resume copy-id")
    with runtime_module.ClaudeRuntime().conversation(tmp_path / "agent", TOOL, fork=installed) as talk:
        talk.send("review")
        (tmp_path / "new.jsonl").write_text("{}\n")  # Claude records the fork beside the copy.
        (tmp_path / "new" / "subagents").mkdir(parents=True)
    command = calls[0]["command"]
    assert command[command.index("--resume") + 1] == "copy-id" and "--fork-session" in command
    assert calls[0]["cwd"] == project and talk.id == "new"
    assert not (tmp_path / "new.jsonl").exists() and not (tmp_path / "new").exists()  # The fork is removed.
    assert transcript.is_file()  # The copy is the reflector's to remove.


def test_pi_runtime_reads_its_json_events(commands, tmp_path):
    calls, outputs = commands
    events = [
        "Warning: creating a new session",
        json.dumps({"type": "session", "id": "pi-1"}),
        json.dumps({"type": "message_end", "message": {"role": "user", "content": []}}),
        json.dumps(
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "done"}]},
            }
        ),
    ]
    outputs += ["\n".join(events), "\n".join(events[1:])]
    with runtime_module.PiRuntime().conversation(tmp_path, TOOL) as talk:
        assert talk.send("review") == "done" and talk.id == "pi-1"
        talk.send("fix")
    first, second = (call["command"] for call in calls)
    assert "--session-id" in first and second[second.index("--session") + 1] == "pi-1"
    assert first[-1].startswith("@") and first[first.index("--tools") + 1].startswith("read,grep,find,ls,mcp__")


def test_cli_runtimes_connect_only_the_submit_tool(commands, tmp_path):
    calls, outputs = commands
    outputs += [
        json.dumps({"subtype": "success", "is_error": False, "result": "ok", "session_id": "s"}),
        json.dumps({"type": "message_end", "message": {"role": "assistant", "content": []}}),
    ]
    with runtime_module.ClaudeRuntime().conversation(tmp_path, TOOL) as talk:
        talk.send("review")
    with runtime_module.PiRuntime().conversation(tmp_path, TOOL) as talk:
        talk.send("review")
        pi = calls[1]["command"]
        extension = Path(pi[pi.index("builtin:mcp") + 2])
        assert TOOL.url in extension.read_text() and '"direct"' in extension.read_text()
    claude = calls[0]["command"]
    assert json.loads(claude[claude.index("--mcp-config") + 1]) == {
        "mcpServers": {"honed": {"type": "http", "url": TOOL.url}}
    }
    assert claude[claude.index("--allowedTools") + 1] == "mcp__honed__submit_reflection"
    assert claude[claude.index("--tools") + 1] == "Read,Grep,Glob"
    assert pi[pi.index("--tools") + 1] == "read,grep,find,ls,mcp__honed__submit_reflection"


def test_antigravity_cannot_connect_the_submit_tool(tmp_path):
    with pytest.raises(ReflectionError, match="submit_reflection"):
        runtime_module.AntigravityRuntime().conversation(tmp_path, TOOL)


def test_to_dict_is_json_ready(agent):
    reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", agent, SCHEMA, evidence="e1")
    data = json.loads(json.dumps(reflection.to_dict()))
    assert data["id"] == reflection.id and data["revision"] == agent.revision and data["attempts"] == 1
    assert data["output"] == [GOOD] and data["evidence"] == {"attached": True, "asked": fingerprint("Review.", SCHEMA)}
    assert "entries" not in data
    # The envelope: how it was made, what it was asked, and where its ratings are (for the session index).
    assert data["mode"] == "attached" and data["trigger"] is None
    assert data["prompt"] == {"name": None, "version": None, "hash": prompt_hash("Review.")}
    assert data["schema"] == schema_hash(SCHEMA)
    assert data["items"] == [{"path": "/0", "ref": GOOD["ref"], "scale": "usefulness", "rating": "harmful"}]
    again = Reflection.from_dict(data, SCHEMA)
    assert (again.mode, again.prompt, again.schema) == ("attached", data["prompt"], data["schema"])


def test_a_prompt_is_known_by_its_name_version_and_hash(tmp_path, agent):
    from honed.reflection.prompts import Prompt

    path = tmp_path / "reflect.md"
    path.write_text("---\nid: reflect\nversion: 2\n---\nReview the configuration.\n")
    prompt = Prompt.load(path)
    assert (prompt.name, prompt.version, prompt.label) == ("reflect", "2", "reflect@2")
    assert prompt.text == "Review the configuration.\n"  # The reflector never sees the frontmatter…
    assert fingerprint(prompt, SCHEMA) == fingerprint("Review the configuration.\n", SCHEMA)  # …nor does the question.
    assert prompt.hash == prompt_hash("Review the configuration.  \r\n\n")  # Whitespace is not a new question.
    assert prompt.hash != prompt_hash("Review the configuration closely.")
    plain = tmp_path / "check.md"
    plain.write_text("Check.\n")
    assert (Prompt.load(plain).name, Prompt.load(plain).version) == ("check", None)
    runtime = Scripted(answer(GOOD))
    reflection = Reflector(runtime=runtime).reflect(prompt, agent, SCHEMA)
    assert reflection.prompt == {"name": "reflect", "version": "2", "hash": prompt.hash}
    assert "Review the configuration." in runtime.sent[0] and "version: 2" not in runtime.sent[0]


def test_cli_runs_get_a_closed_stdin(tmp_path):
    # `cat` reads stdin until it closes; an inherited, never-closing stdin hung Pi for many minutes.
    assert runtime_module._run(["cat"], cwd=tmp_path, timeout=5) == ""


def test_handoff_on_a_root_thread_leaves_its_subagents_out(agent, tmp_path):
    from honed.sessions import Event, Session, SourceReference

    def message(number, run, text, *, role="user", parent=None):
        return Event(id=f"e{number}", source_id="fixture", harness="claude", session_id="s", run_id=run,
                     parent_run_id=parent, kind="message", role=role, text=text,
                     reference=SourceReference(path="/fixture.jsonl", generation=0, byte_offset=number * 100,
                                               byte_length=100, line=number + 1))

    session = Session.from_events([
        message(0, "main", "Fix the retry bug"), message(1, "main", "Fixed.", role="assistant"),
        message(2, "sub", "Find the retry code", parent="main"), message(3, "sub", "In retry.py", role="assistant",
                                                                          parent="main"),
    ])
    root = session.root_thread
    reflection = Reflector(runtime=Scripted(answer(GOOD)), home=tmp_path).handoff("Review.", root, agent, SCHEMA)
    assert reflection.evidence["thread"] == "main" and reflection.evidence["session"] == "s"
    assert reflection.evidence["events"] == 2
    assert reflection.evidence["digest"] == events_digest(root) != events_digest(session)
    whole = Reflector(runtime=Scripted(answer(GOOD)), home=tmp_path).handoff("Review.", session, agent, SCHEMA)
    assert "thread" not in whole.evidence and whole.evidence["events"] == 4


def test_an_installed_prompts_stamp_is_not_part_of_the_question(tmp_path):
    from honed.reflection.prompts import Prompt

    plain, stamped = tmp_path / "a.md", tmp_path / "b.md"
    plain.write_text("---\nid: reflect\nversion: 1\n---\nReview.\n")
    stamped.write_text("---\nid: reflect\nversion: 1\n---\n<!-- honed-evolve 0.2.0 -->\nReview.\n")
    assert Prompt.load(stamped).text == Prompt.load(plain).text == "Review.\n"
