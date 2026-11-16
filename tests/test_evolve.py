"""/evolve end to end, without model calls: install the evolve skill into a project for a harness, run a round with
scripted runtimes, amend the proposal, apply it, and pick up a hand edit on the next round."""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from thearc import evolve
from thearc.curation import Curator
from thearc.reflection import Reflector
from thearc.resources import MetaAgent, install

from .test_curation import ScriptedCurator, edit
from .test_reflection import GOOD, SCHEMA, Scripted, answer


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(agent, tmp_path, monkeypatch):
    """A git project with a hand-made Claude configuration (the ``agent`` fixture installed), and a Claude session
    store holding its sessions."""
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    install(agent, "claude", root)
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Project")
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    store = home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
    store.mkdir(parents=True)
    now = "2099-01-01T10:00:00Z"
    for native, text in (("s1", "Fix the retry bug"), ("s2", "Add a test"), ("s3", "Refactor retries")):
        (store / f"{native}.jsonl").write_text(json.dumps(
            {"type": "user", "sessionId": native, "cwd": str(root.resolve()), "timestamp": now,
             "message": {"role": "user", "content": text}}) + "\n")
    evolved = {"type": "assistant", "sessionId": "s4", "cwd": str(root.resolve()), "timestamp": now,
               "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "thearc evolve --json"}}]}}
    (store / "s4.jsonl").write_text(json.dumps(evolved) + "\n")  # An /evolve run: never evidence.
    return root


def scripted_recipe(edits, seen):
    """A recipe like the default one, with scripted reflector and curator."""

    def propose(evolution):
        sessions = evolution.sessions(since=timedelta(days=36500))
        seen["sessions"] = sorted(s.metadata.native_id for s in sessions)
        if not edits:
            return evolution.nothing("nothing to change")
        candidate = evolution.candidate()
        reflection = Reflector(runtime=Scripted(answer(GOOD))).reflect("Review.", candidate, SCHEMA)
        candidate.add_reflection(reflection)

        def script(root, tool):
            for old, new, reason in edits:
                edit(root, old, new)
                assert not tool("commit_change", message=reason)["error"]
            tool("finish", summary="Done.")

        Curator(runtime=ScriptedCurator(script)).curate("Fix it.", [reflection], candidate)
        return candidate.reload()

    return propose


WORKSPACE = ".thearc/workspace"


def test_install_adopts_the_harness_configuration_beside_the_evolve_skill(project):
    setup = evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # A repository of its own…
    assert git(project, "ls-files", "--stage", WORKSPACE).startswith("160000")  # …added as a submodule.
    assert "path = .thearc/workspace" in (project / ".gitmodules").read_text()
    assert not git(project, "for-each-ref", "refs/heads/thearc")  # No workspace branches in the project.
    assert setup.agent.name == "app" and set(setup.agent.skills) == {"retry"}
    assert setup.agent.ignore == ["skill:evolve", "skill:reflect"] and not setup.agent.frozen  # Never evolved.
    skill = project / ".claude/skills/evolve"
    assert 'RUNTIME = "claude"' in (skill / "evolve.py").read_text() and (skill / "prompts/curate.md").is_file()
    assert 'HARNESS = "claude"' in (skill / "evolve.py").read_text()  # Only Claude's sessions are read.
    assert "`thearc evolve diff`" in (skill / "SKILL.md").read_text()
    record = json.loads((project / ".thearc/installed.json").read_text())["agents"]
    assert ".claude/skills/evolve/SKILL.md" in record["thearc-evolve"]["harnesses"]["claude"]["files"]
    ignored = set((project / ".gitignore").read_text().splitlines())
    assert "/.thearc/cache/" in ignored and "/.thearc/workspace/" not in ignored
    assert git(project, "rev-parse", "--abbrev-ref", "HEAD") == "main"  # The project's checkout is untouched.
    status = evolve.status(project)
    assert status["installed"] == "1.0.0" and status["runtime"] == "claude" and status["harness"] == "claude"


def test_install_needs_git_and_no_evolve_skill_of_the_projects_own(agent, tmp_path):
    with pytest.raises(ValueError, match="git"):
        evolve.install_evolve_skill(tmp_path, "claude")
    git(tmp_path, "init", "-q")
    mine = agent.model_copy(update={"skills": {"evolve": agent.skills["retry"].model_copy(update={"name": "evolve"})}})
    install(mine, "claude", tmp_path)
    with pytest.raises(ValueError, match="named 'evolve'"):
        evolve.install_evolve_skill(tmp_path, "claude")


def test_a_round_proposes_amends_and_applies(project):
    evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe_text = recipe.read_text()
    seen = {}
    proposal = evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries are enough")], seen))
    assert seen["sessions"] == ["s1", "s2", "s3"]  # The /evolve run is left out.
    assert proposal.version == "1.0.1" and proposal.base == "1.0.0" and "max 2" in proposal.diff
    (change,) = proposal.changes
    assert change.target == "skill:retry#Retry > Limits" and change.reason == "Two tries are enough"
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "proposal/1.0.1"
    assert "max 3" in (project / ".claude/skills/retry/SKILL.md").read_text()  # Not installed yet.
    assert evolve.status(project)["proposal"] == "1.0.1"
    with pytest.raises(ValueError, match="open"):
        evolve.run(project, recipe=scripted_recipe([], {}))

    edit(workspace, "max 2", "max 2, then report")  # The user asks the agent for an amendment.
    assert evolve.amend(project, "Say what happens after the last try")
    amended = evolve.diff(project)
    assert ("skill:retry#Retry > Limits", "user") in {(c.target, c.author) for c in amended.changes}
    assert "max 2, then report" in amended.diff

    applied = evolve.apply(project)
    assert applied.version == "1.0.1" and applied.harnesses == ("claude",)
    assert "max 2, then report" in (project / ".claude/skills/retry/SKILL.md").read_text()
    assert recipe.read_text() == recipe_text  # The evolve skill is not the configuration's: left alone.
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    branches = git(workspace, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    assert "release/1.0.1" in branches and not any("proposal" in b for b in branches)
    assert git(project, "status", "--porcelain", WORKSPACE).endswith("M .thearc/workspace")  # The submodule moved.
    assert MetaAgent.from_workspace(workspace).version == "1.0.1"

    installed = project / ".claude/skills/retry/SKILL.md"  # A hand edit before the next round…
    installed.write_text(installed.read_text().replace("ex1", "ex1, by hand"))
    nothing = evolve.run(project, recipe=scripted_recipe([], seen))
    assert nothing.version is None and nothing.reason == "nothing to change"
    assert nothing.drift == ("modified skill:retry#Retry > Examples",)  # …is committed, not overwritten.
    assert "ex1, by hand" in MetaAgent.from_workspace(workspace).skills["retry"].instructions


def test_discard_drops_the_proposal_and_its_edits(project):
    evolve.install_evolve_skill(project, "claude")
    workspace = project / WORKSPACE
    evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries")], {}))
    edit(workspace, "max 2", "max 9")
    assert evolve.discard(project) == "1.0.1"
    assert git(workspace, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "max 3" in (workspace / "skills/retry/SKILL.md").read_text()


def test_ignoring_leaves_the_installed_skill_alone(project):
    evolve.install_evolve_skill(project, "claude")
    agent = evolve.ignore(project, ["skill:retry"])
    assert "retry" not in agent.skills and (project / ".claude/skills/retry/SKILL.md").is_file()
    assert evolve.status(project)["ignored"] == ["skill:evolve", "skill:reflect", "skill:retry"]
    back = evolve.ignore(project, ["skill:retry"], remove=True)
    assert "retry" in back.skills  # Adopted again from the installation.


def test_the_default_recipe_is_the_installed_one_and_waits_for_enough_sessions(project, tmp_path):
    evolve.install_evolve_skill(project, "claude")
    for transcript in (tmp_path / "claude-home/projects").rglob("*.jsonl"):
        transcript.unlink()
    proposal = evolve.run(project)  # .claude/skills/evolve/evolve.py, its prompts read from next to it.
    assert proposal.version is None and proposal.reason == "0 session(s) in the last 7 days; 3 needed"
    assert not (project / ".claude/skills/evolve/__pycache__").exists()
    page = Path(proposal.progress).read_text()  # The round's flow, as it ran: it stopped at "too few".
    assert 'class="chip success">success' in page and "TooFew" in page and "0 claude session(s)" in page


def test_the_default_recipe_shows_its_progress_and_proposes(project, monkeypatch):
    import dataclasses

    import thearc.curation
    import thearc.reflection

    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"}]}

    failed: list[str] = []

    class ScriptedReflector:  # The recipe's Reflector(RUNTIME), without a model; the first reflection fails.
        def __init__(self, harness, **options):
            self.harness, self.options = harness, options

        def handoff(self, prompt, session, agent, schema, *, provenance=None, focus=None, judged_from=None):
            if not failed:
                failed.append(session.id)
                raise thearc.reflection.ReflectionError("no_submission", "ended without a submission")
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, evidence={"session": session.id, "provenance": provenance})

    def curator(harness, **options):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")
        return Curator(runtime=ScriptedCurator(script))

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    evolve.install_evolve_skill(project, "claude")
    proposal = evolve.run(project)
    assert proposal.version == "1.0.1" and proposal.sessions == 3 and "max 2" in proposal.diff
    page = Path(proposal.progress).read_text()
    assert page.count('class="chip success">success') == 5  # FindSessions, Partition, Source, Reflect, Curate.
    assert "3 claude session(s)" in page and "1 rating(s) (1 harmful)" in page and "Two tries are enough" in page
    assert f"{failed[0]}: no reflection (ended without a submission); going on without it" in page
    assert (project / ".thearc/cache/progress.html").read_text() == page


def test_a_project_evolves_one_harness(project):
    evolve.install_evolve_skill(project, "claude")
    with pytest.raises(ValueError, match="already evolves its claude configuration"):
        evolve.install_evolve_skill(project, "codex")
    assert evolve.install_evolve_skill(project, "claude", runtime="codex").runtime == "codex"  # Reinstalled.
    assert 'RUNTIME = "codex"' in (project / ".claude/skills/evolve/evolve.py").read_text()


def test_the_command_line(project, tmp_path):
    from click.testing import CliRunner

    from thearc.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "install", "self-evolve", "--project", str(project), "--harness", "claude"])
    assert result.exit_code == 0, result.output
    assert "app 1.0.0 is set up for evolution in claude" in result.output
    for transcript in (tmp_path / "claude-home/projects").rglob("*.jsonl"):
        transcript.unlink()
    result = runner.invoke(main, ["evolve", "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["version"] is None
    result = runner.invoke(main, ["evolve", "--project", str(project), "status", "--json"])
    assert json.loads(result.output)["runtime"] == "claude"
    result = runner.invoke(main, ["evolve", "--project", str(project), "apply"])
    assert result.exit_code == 1 and "no proposal is open" in result.output
    result = runner.invoke(main, ["skills", "uninstall", "self-evolve", "--project", str(project)])
    assert result.exit_code == 0 and not (project / ".claude/skills/evolve").exists()
    assert (project / ".claude/skills/retry/SKILL.md").is_file()


def test_a_teammate_gets_the_workspace_with_the_project(project, tmp_path, monkeypatch):
    for key, value in (("COUNT", "1"), ("KEY_0", "protocol.file.allow"), ("VALUE_0", "always")):
        monkeypatch.setenv(f"GIT_CONFIG_{key}", value)  # Local paths as remotes, for the test.
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    evolve.install_evolve_skill(project, "claude", remote=str(shared))
    assert git(shared, "rev-parse", "main")  # Pushed at install.
    assert f"url = {shared}" in (project / ".gitmodules").read_text()
    git(project, "add", "-A")
    git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Set up evolution")
    origin = tmp_path / "project.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(project, "push", "-q", str(origin), "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "-b", "main", str(origin), str(clone))  # origin's HEAD may name master.
    agent = evolve.init_from_remote(clone)
    assert agent.version == "1.0.0" and set(agent.skills) == {"retry"}
    assert git(clone / WORKSPACE, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert evolve.status(clone)["remote"] == str(shared)


def test_apply_pushes_the_workspace_when_asked(project, tmp_path, monkeypatch):
    evolve.install_evolve_skill(project, "claude")
    evolve.run(project, recipe=scripted_recipe([("max 3", "max 2", "Two tries")], {}))
    with pytest.raises(ValueError, match="no remote"):
        evolve.apply(project, push=True)
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    evolve.setup.set_remote(project, str(shared))
    assert f"url = {shared}" in (project / ".gitmodules").read_text()
    applied = evolve.apply(project, push=True)
    assert applied.pushed == str(shared)
    assert git(shared, "rev-parse", "main") == git(project / WORKSPACE, "rev-parse", "main")
    assert git(shared, "rev-parse", "--verify", "release/1.0.1")


def test_a_round_can_be_estimated_first_and_stops_over_its_budget(project, monkeypatch):
    import thearc.reflection

    def no_model(*args, **kwargs):
        raise AssertionError("an estimate makes no model call")

    monkeypatch.setattr(thearc.reflection, "Reflector", no_model)
    evolve.install_evolve_skill(project, "claude")
    estimated = evolve.run(project, estimate_only=True)
    assert estimated.version is None and estimated.estimate is not None and estimated.estimate.sessions == 3
    assert estimated.estimate.cost is None and "no cost history yet" in estimated.reason
    assert estimated.estimate.tokens > 0 and "Stop" in Path(estimated.progress).read_text()
    assert not list((project / ".thearc/cache/rounds").glob("*/candidates"))  # Nothing was set up for reflecting.

    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe.write_text(recipe.read_text().replace("BUDGET: float | None = None", "BUDGET: float | None = 0.01"))
    monkeypatch.setattr("thearc.evolve.round.estimate", lambda *a, **k: evolve.Estimate(3, 900, 0.5, "from history"))
    over = evolve.run(project)
    assert over.version is None and over.reason == "estimated at $0.50, over the budget of $0.01"


def test_a_later_round_reuses_the_reflections_of_one_that_was_discarded(project, monkeypatch):
    import dataclasses

    import thearc.curation
    import thearc.reflection
    from thearc.reflection.reflector import events_digest, fingerprint

    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"}]}
    reflected: list[str] = []

    class ScriptedReflector:  # Records the evidence a real handoff records, so the reflection can be matched later.
        def __init__(self, harness, **options):
            pass

        def handoff(self, prompt, session, agent, schema, *, provenance=None, focus=None, judged_from=None):
            reflected.append(session.id)
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, evidence={
                "session": session.id, "digest": events_digest(session), "asked": fingerprint(prompt, schema)})

    def curator(harness, **options):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")
        return Curator(runtime=ScriptedCurator(script))

    class Frozen(datetime):  # Every round starts in the same second: each still gets a round of its own.
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    monkeypatch.setattr("thearc.evolve.round.datetime", Frozen)
    evolve.install_evolve_skill(project, "claude")
    first = evolve.run(project)
    assert first.version == "1.0.1" and len(reflected) == 3
    evolve.discard(project)

    estimated = evolve.run(project, estimate_only=True)
    assert (estimated.estimate.sessions, estimated.estimate.reused) == (0, 3)
    assert "0 session(s) (and 3 reflected on before, reused)" in str(estimated.estimate)

    again = evolve.run(project)
    assert again.version == "1.0.1" and "max 2" in again.diff
    assert len(reflected) == 3  # No reflector ran: the discarded round's reflections were reused.
    assert "from an earlier round (no model call)" in Path(again.progress).read_text()
    stamp = Path(first.progress).parent.name  # Rounds: the first, the estimate, this one.
    assert Path(again.progress).parent.name == f"{stamp}-3"

    recipe = project / ".claude/skills/evolve/prompts/reflect.md"
    recipe.write_text(recipe.read_text() + "\nAlso note anything slow.\n")  # A new question: reflect again.
    evolve.discard(project)
    evolve.run(project)
    assert len(reflected) == 6


def test_the_recipe_can_reflect_on_root_threads_only(project, monkeypatch):
    import dataclasses

    import thearc.curation
    import thearc.reflection
    from thearc.reflection.reflector import events_digest, fingerprint
    from thearc.sessions import Thread

    store = next((Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects").iterdir())
    subagent = {"type": "user", "sessionId": "s1", "cwd": str(project.resolve()), "timestamp": "2099-01-01T10:00:01Z",
                "message": {"role": "user", "content": "Find the retry code"}}
    (store / "s1" / "subagents").mkdir(parents=True)
    (store / "s1" / "subagents" / "agent-a.jsonl").write_text(json.dumps(subagent) + "\n")
    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"}]}
    handed: list = []

    class ScriptedReflector:  # Records the evidence a real handoff records, so the reflection can be matched later.
        def __init__(self, harness, **options):
            pass

        def handoff(self, prompt, history, agent, schema, *, provenance=None, focus=None, judged_from=None):
            handed.append(history)
            owner = history.session if isinstance(history, Thread) else history
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, evidence={
                "session": owner.id, "digest": events_digest(history), "asked": fingerprint(prompt, schema)})

    def curator(harness, **options):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")
        return Curator(runtime=ScriptedCurator(script))

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    evolve.install_evolve_skill(project, "claude")
    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe.write_text(recipe.read_text().replace('REFLECT_ON = "session"', 'REFLECT_ON = "root"'))
    first = evolve.run(project)
    assert first.version == "1.0.1" and len(handed) == 3
    (thread,) = [h for h in handed if isinstance(h, Thread)]  # Only s1 has a subagent; the others go whole.
    assert thread.session.metadata.native_id == "s1" and thread.parent_id is None
    assert len(thread.events) == len(thread.session.events) - 1
    assert "(root thread" in Path(first.progress).read_text()
    evolve.discard(project)

    estimated = evolve.run(project, estimate_only=True)  # The root thread's reflection is found again.
    assert (estimated.estimate.sessions, estimated.estimate.reused) == (0, 3)


def test_the_recipe_hands_reflectors_the_failure_findings_and_can_select_by_them(project, monkeypatch):
    import dataclasses

    import thearc.curation
    import thearc.reflection

    store = next((Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects").iterdir())
    cwd, now = str(project.resolve()), "2099-01-01T10:00:00Z"
    records = [{"type": "user", "sessionId": "s5", "cwd": cwd, "timestamp": now,
                "message": {"role": "user", "content": "Fix the retry bug in the client"}}]
    for number, (command, failed) in enumerate([("sed -i s/3/2/ src/retry.py", False), ("pytest -q", True)]):
        records.append({"type": "assistant", "sessionId": "s5", "cwd": cwd, "timestamp": now, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": f"t{number}", "name": "Bash",
                                              "input": {"command": command}}]}})
        records.append({"type": "user", "sessionId": "s5", "cwd": cwd, "timestamp": now, "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{number}", "content": "FAILED",
                                         "is_error": failed}]}})
    records.append({"type": "assistant", "sessionId": "s5", "cwd": cwd, "timestamp": now,
                    "message": {"role": "assistant", "content": "Fixed: all tests pass."}})
    (store / "s5.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))
    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful",
                                               "failure_mode": "claim_contradicted"}]}
    focused: dict = {}

    class ScriptedReflector:
        def __init__(self, harness, **options):
            pass

        def handoff(self, prompt, session, agent, schema, *, provenance=None, focus=None, judged_from=None):
            focused[session.metadata.native_id] = focus
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, evidence={"session": session.id})

    def curator(harness, **options):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")
        return Curator(runtime=ScriptedCurator(script))

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    evolve.install_evolve_skill(project, "claude")
    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe.write_text(recipe.read_text().replace("MIN_SESSIONS = 3", "MIN_SESSIONS = 1"))
    proposal = evolve.run(project)
    assert proposal.version == "1.0.1" and set(focused) == {"s1", "s2", "s3", "s5"}
    assert "claim_contradicted" in focused["s5"] and "may be false positives" in focused["s5"]
    assert focused["s1"] is None  # Nothing found: no focus.
    assert "s5" in Path(proposal.progress).read_text() and "premature_done" in Path(proposal.progress).read_text()
    evolve.discard(project)

    focused.clear()
    recipe.write_text(recipe.read_text().replace('SELECT = "all"', 'SELECT = "findings"'))
    evolve.run(project)
    assert set(focused) == {"s5"}  # Only the session with findings is reflected on.


def test_a_round_sources_existing_reflections_and_reflects_only_on_the_rest(project, monkeypatch):
    """s1 is covered by an in-session reflection, s2 partly (a long tail after it), s3 not at all: the round takes
    the two existing reflections, hands off s3 whole and s2's tail, and curates from all four."""
    import dataclasses

    import thearc.curation
    import thearc.reflection
    from thearc.reflection import Prompt
    from thearc.sessions import Sessions

    store = next((Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects").iterdir())
    lines = [json.dumps({"type": "user" if i % 2 else "assistant", "sessionId": "s2", "cwd": str(project.resolve()),
                         "timestamp": f"2099-01-01T10:{i:02d}:00Z", "uuid": f"s2-{i}",
                         "message": {"role": "user" if i % 2 else "assistant", "content": f"step {i}"}})
             for i in range(1, 31)]
    (store / "s2.jsonl").write_text("\n".join(lines) + "\n")
    lesson = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
              "key_insight": "k", "ratings": [{"ref": "skills/retry/SKILL.md#L17", "usefulness": "harmful"}]}
    handed: list = []
    curated: list = []

    class ScriptedReflector:
        def __init__(self, harness, **options):
            pass

        def handoff(self, prompt, history, agent, schema, *, provenance=None, focus=None, judged_from=None):
            handed.append((history.metadata.native_id, judged_from, focus))
            reflection = Reflector(runtime=Scripted([lesson])).reflect(prompt, agent, schema)
            return dataclasses.replace(reflection, mode="handoff", evidence={
                **reflection.evidence, "session": history.id, "to_event": history.events[-1].id,
                "from_event": judged_from or history.events[0].id})

    def curator(harness, **options):
        def script(root, tool):
            edit(root, "max 3", "max 2")
            assert not tool("commit_change", message="Two tries are enough")["error"]
            tool("finish", summary="Lowered the limit.")

        class Recording(Curator):
            def curate(self, prompt, reflections, candidate, **kwargs):
                curated.append(sorted(r.mode for r in reflections))
                return super().curate(prompt, reflections, candidate, **kwargs)

        return Recording(runtime=ScriptedCurator(script))

    monkeypatch.setattr(thearc.reflection, "Reflector", ScriptedReflector)
    monkeypatch.setattr(thearc.curation, "Curator", curator)
    evolve.install_evolve_skill(project, "claude")
    release = MetaAgent.from_workspace(project / WORKSPACE)
    prompt = Prompt.load(project / ".claude/skills/evolve/prompts/reflect.md")
    with Sessions() as index:  # The session index, where /reflect keeps in-session reflections.
        index.add(store, harness="claude")
        s1, s2 = index.get("s1"), index.get("s2")
        for session, upto in ((s1, s1.events[-1].id), (s2, s2.events[4].id)):
            made = Reflector(runtime=Scripted([lesson])).reflect(prompt, release, evolve.SCHEMA)
            index.add_reflection(dataclasses.replace(made, mode="in-session", evidence={
                **made.evidence, "native_session": session.metadata.native_id, "from_event": session.events[0].id,
                "to_event": upto}))

    proposal = evolve.run(project)
    assert proposal.version == "1.0.1"
    assert sorted((native, judged is not None) for native, judged, _ in handed) == [("s2", True), ("s3", False)]
    (tail,) = [focus for native, _, focus in handed if native == "s2"]
    assert "last 25 event(s)" in tail
    assert curated == [["handoff", "handoff", "in-session", "in-session"]]
    page = Path(proposal.progress).read_text()
    assert "1 covered, 1 partly covered, 1 not reflected on" in page and "sourced 2 reflection(s)" in page
    with Sessions() as index:  # The round's own reflections are kept there too.
        assert index.index.connection.execute("SELECT count(*) FROM reflections").fetchone()[0] == 4
    evolve.discard(project)

    handed.clear()
    recipe = project / ".claude/skills/evolve/evolve.py"
    recipe.write_text(recipe.read_text().replace("SOURCE = True", "SOURCE = False"))
    evolve.run(project)
    assert sorted(native for native, _, _ in handed) == ["s1", "s2", "s3"]  # Whatever covers them.
    evolve.discard(project)
    handed.clear()

    prompt_file = project / ".claude/skills/evolve/prompts/reflect.md"  # Same version, new text: by hash, a new
    prompt_file.write_text(prompt_file.read_text() + "\nAlso note anything slow.\n")  # question.
    recipe.write_text(recipe.read_text().replace("SOURCE = False", "SOURCE = True")
                      .replace('SOURCE_MATCH = "version"', 'SOURCE_MATCH = "hash"'))
    evolve.run(project)
    assert sorted(native for native, _, _ in handed) == ["s1", "s2", "s3"]
