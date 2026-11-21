"""The reflect skill: installed with self-evolve or on its own, its begin/submit commands, and an in-session
reflection joining its session in the index."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from honed import evolve
from honed.cli import main
from honed.evolve import live
from honed.sessions import Sessions
from honed.resources import AgentConfig, install

from .test_evolve import WORKSPACE, git, project  # noqa: F401 -- the fixture

LESSON = {"reasoning": "r", "error_identification": "e", "root_cause_analysis": "c", "correct_approach": "a",
          "key_insight": "k"}


def installed_line(project, relative, text):
    """The 1-based line of ``text`` in an installed file."""
    lines = (project / relative).read_text().splitlines()
    return lines.index(text) + 1


def test_self_evolve_installs_the_reflect_skill(project):
    evolve.install_evolve_skill(project, "claude")
    skill = (project / ".claude/skills/reflect/SKILL.md").read_text()
    assert "honed reflect begin" in skill and "claude instructions" in skill  # HARNESS filled in.
    record = json.loads((project / ".honed/installed.json").read_text())["agents"]["honed-evolve"]["harnesses"]
    assert ".claude/skills/reflect/SKILL.md" in record["claude"]["files"]
    assert not (project / ".honed/reflections").exists()  # Reflections live in the session index.
    agent = AgentConfig.from_workspace(project / WORKSPACE)
    drift = agent.sync_installation(project, ["claude"])
    assert not drift.entries and drift.agent.revision == agent.revision
    assert "reflect" not in agent.skills and not any("honed" in (hook.command or "") for hook in agent.hooks.values())

    evolve.uninstall_evolve_skill(project)
    assert not (project / ".claude/skills/reflect").exists() and not (project / ".claude/skills/evolve").exists()



@pytest.mark.parametrize("harness", ["codex", "pi", "opencode"])
def test_self_evolve_installs_the_reflect_skill_for_every_harness(agent, tmp_path, harness):
    from honed.resources.harness import get_harness

    git(tmp_path, "init", "-q", "-b", "main")
    install(agent, harness, tmp_path)
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Project")
    evolve.install_evolve_skill(tmp_path, harness)
    layout = get_harness(harness)
    assert (tmp_path / layout.location("skills") / "reflect/SKILL.md").is_file()


def test_a_projects_own_reflect_skill_is_refused_unless_left_out(agent, tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    theirs = agent.skills["retry"].model_copy(update={"name": "reflect"})
    mine = agent.model_copy(update={"skills": {"reflect": theirs}})
    install(mine, "claude", tmp_path)
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Project")
    with pytest.raises(ValueError, match="--no-reflect"):
        evolve.install_evolve_skill(tmp_path, "claude")
    evolve.install_evolve_skill(tmp_path, "claude", reflect=False)
    assert "honed reflect" not in (tmp_path / ".claude/skills/reflect/SKILL.md").read_text()  # Theirs, untouched.


def test_reinstalling_upgrades_a_project_set_up_before_the_reflect_skill(project):
    evolve.install_evolve_skill(project, "claude", reflect=False)
    workspace = project / WORKSPACE
    evolve.ignore(project, ["skill:reflect"], remove=True)  # As a project set up before it was.
    assert "skill:reflect" not in AgentConfig.from_workspace(workspace).ignore
    commits = int(git(workspace, "rev-list", "--count", "HEAD"))
    evolve.install_evolve_skill(project, "claude")
    assert AgentConfig.from_workspace(workspace).ignore[-1] == "skill:reflect"
    assert int(git(workspace, "rev-list", "--count", "HEAD")) == commits + 1
    assert git(workspace, "log", "-1", "--format=%s").startswith("Ignore skill:reflect")
    assert (project / ".claude/skills/reflect/SKILL.md").is_file()


def test_begin_and_submit_an_in_session_reflection(project, session_index):
    evolve.install_evolve_skill(project, "claude")
    begun = live.begin(project, trigger="tests green")
    assert begun.marker.startswith("honed-reflect:") and "Review this project's agent configuration" in str(begun)
    assert "id: reflect" not in begun.question  # The recipe's prompt, without its frontmatter.
    copies = project / begun.files
    assert (copies / "skills/retry/SKILL.md").is_file()

    limit = installed_line(project, ".claude/skills/retry/SKILL.md", "max 3")
    wrong = {**LESSON, "ratings": [{"ref": ".claude/skills/retry/SKILL.md#L999", "usefulness": "harmful"}]}
    with pytest.raises(ValueError, match="Fix these"):
        live.submit(project, begun.marker, wrong)
    answer = {**LESSON, "ratings": [
        {"ref": f".claude/skills/retry/SKILL.md#L{limit}", "usefulness": "harmful"},  # An installed path…
        {"ref": f"{begun.files}/skills/retry/SKILL.md#L7", "usefulness": "neutral"}]}  # …and a copy.
    accepted = live.submit(project, begun.marker, answer)
    reflection = accepted.reflection
    assert reflection.mode == "in-session" and reflection.trigger == "tests green" and reflection.attempts == 2
    own = AgentConfig.from_workspace(project / WORKSPACE).files().files["skills/retry/SKILL.md"].splitlines()
    first, second = (str(item.ref) for item in reflection)
    assert own[int(first.rpartition("#L")[2]) - 1] == "max 3"  # Mapped by its text to the release's line.
    assert second == "skills/retry/SKILL.md#L7"
    assert reflection.prompt["name"] == "reflect" and reflection.prompt["text"].startswith("Review")
    assert not accepted.bound and "1 harmful" in str(accepted)  # Its marker is in no transcript yet.
    assert accepted.index == session_index.resolve()  # $HONED_INDEX: where sessions are indexed.
    with Sessions() as index:
        stored = index.index.connection.execute("SELECT id, session_id FROM reflections").fetchall()
    assert [tuple(row) for row in stored] == [(reflection.id, None)]
    with Sessions() as index:  # A query finds it before its session is indexed: by its ID, and by its project.
        (match,) = index.query("select:reflection bound:no")
        assert match.session is None and match.reflection["id"] == reflection.id
        assert index.count(f'select:reflection reflection:{reflection.id[:16]} cwd:"{project}"') == 1
        assert index.count("select:reflection bound:yes") == index.count("select:reflection harness:claude") == 0
    with pytest.raises(ValueError, match="no reflection was begun"):  # One answer per marker.
        live.submit(project, begun.marker, answer)


def test_an_in_session_reflection_joins_its_session_and_the_next_round_sources_it(project):
    evolve.install_evolve_skill(project, "claude")
    begun = live.begin(project)
    limit = installed_line(project, ".claude/skills/retry/SKILL.md", "max 3")
    answer = {**LESSON, "ratings": [{"ref": f".claude/skills/retry/SKILL.md#L{limit}", "usefulness": "harmful"}]}
    store = next((Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects").iterdir())
    lines = [{"type": "user", "message": {"role": "user", "content": "Fix the retry bug"}},
             {"type": "assistant", "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "honed reflect begin"}}]}},
             {"type": "user", "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "b", "content": str(begun)}]}},
             {"type": "assistant", "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "s", "name": "Bash",
                  "input": {"command": f"honed reflect submit {begun.marker} --file r.json"}}]}}]
    (store / "s5.jsonl").write_text("".join(json.dumps({**line, "sessionId": "s5", "cwd": str(project.resolve()),
                                                         "timestamp": f"2099-01-01T11:00:0{i}Z", "uuid": f"u{i}"})
                                            + "\n" for i, line in enumerate(lines)))
    accepted = live.submit(project, begun.marker, answer)
    assert accepted.bound
    with Sessions() as index:  # The index sessions are indexed in: the reflection is there, joined to s5.
        (match,) = index.query("select:reflection mode:in-session")
    kept = match.reflection["data"]
    assert match.session.native_id == "s5" and kept["evidence"]["native_session"] == "s5"
    assert kept["evidence"]["events"] == 1  # The user's request: the reflection's own turns are not evidence.


def test_the_reflect_commands(project, session_index):
    evolve.install_evolve_skill(project, "claude")
    runner = CliRunner()
    begun = runner.invoke(main, ["reflect", "begin", "--project", str(project), "--json"])
    assert begun.exit_code == 0, begun.output
    marker = json.loads(begun.output)["marker"]
    bad = runner.invoke(main, ["reflect", "submit", marker, "--project", str(project)], input="{")
    assert bad.exit_code == 1 and "not JSON" in bad.output
    limit = installed_line(project, ".claude/skills/retry/SKILL.md", "max 3")
    answer = {**LESSON, "ratings": [{"ref": f".claude/skills/retry/SKILL.md#L{limit}", "usefulness": "helpful"}]}
    good = runner.invoke(main, ["reflect", "submit", marker, "--project", str(project)], input=json.dumps(answer))
    assert good.exit_code == 0 and "Accepted reflection_" in good.output
    assert "Kept in" in good.output and str(session_index.resolve()) in good.output


def test_status_shows_the_reflect_skill_and_the_reflections(project):
    evolve.install_evolve_skill(project, "claude")
    begun = live.begin(project)
    limit = installed_line(project, ".claude/skills/retry/SKILL.md", "max 3")
    live.submit(project, begun.marker, {**LESSON, "ratings": [
        {"ref": f".claude/skills/retry/SKILL.md#L{limit}", "usefulness": "harmful"}]})
    state = evolve.status(project)
    assert state["reflect"]
    assert (state["reflections"], state["unbound"]) == (1, 1)  # Its marker is in no transcript yet.
    assert state["sessions_reflected"] == "0 of 4 indexed session(s) on 1.0.0"  # s4 ran /evolve.


def test_reflect_installs_on_its_own_for_every_harness_and_judges_what_is_installed(agent, tmp_path):
    from honed.evolve.setup import install_reflect_skill, uninstall_reflect_skill
    from honed.resources.harness import get_harness, installed_agents

    git(tmp_path, "init", "-q", "-b", "main")
    harnesses = ["claude", "codex", "pi", "opencode", "antigravity"]
    for harness in harnesses:
        install(agent, harness, tmp_path)
    assert install_reflect_skill(tmp_path, harnesses) == harnesses
    for harness in harnesses:
        layout = get_harness(harness)
        skill = (tmp_path / layout.location("skills") / "reflect/SKILL.md").read_text()
        assert f"honed reflect begin --harness {harness}" in skill
    assert set(installed_agents(tmp_path)["honed-reflect"]) == set(harnesses)

    # No evolution workspace: the reflection judges the configuration installed for its harness, honed's skills
    # left out, and asks the default question.
    begun = live.begin(tmp_path, harness="codex")
    copies = tmp_path / begun.files
    assert (copies / "skills/retry/SKILL.md").is_file() and not (copies / "skills/reflect").exists()
    assert begun.question == live.default_prompt().text
    limit = installed_line(tmp_path, ".codex/skills/retry/SKILL.md", "max 3")
    accepted = live.submit(tmp_path, begun.marker, {**LESSON, "ratings": [
        {"ref": f".codex/skills/retry/SKILL.md#L{limit}", "usefulness": "helpful"}]})
    assert accepted.reflection.evidence["harness"] == "codex" and accepted.reflection.prompt["name"] == "reflect"
    with pytest.raises(ValueError, match="--harness"):  # Several installed: which one?
        live.begin(tmp_path)

    # The project's own agent is still what imports: honed's tools never are.
    assert AgentConfig.from_installation("claude", tmp_path).name == agent.name

    assert uninstall_reflect_skill(tmp_path, ["pi"]) == ["pi"]
    assert not (tmp_path / ".pi/skills/reflect").exists() and (tmp_path / ".codex/skills/reflect").exists()
    uninstall_reflect_skill(tmp_path)
    assert "honed-reflect" not in installed_agents(tmp_path)


def test_self_evolve_takes_over_a_reflect_skill_installed_on_its_own(project):
    from honed.evolve.setup import install_reflect_skill
    from honed.resources.harness import installed_agents

    install_reflect_skill(project, ["claude"])
    git(project, "add", "-A")
    git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "reflect")
    evolve.install_evolve_skill(project, "claude")
    agents = installed_agents(project)
    assert "honed-reflect" not in agents and "claude" in agents["honed-evolve"]
    assert install_reflect_skill(project, ["claude"]) == []  # Has it with the evolve skill.
    begun = live.begin(project)  # The harness the project evolves: its release and the recipe's question.
    assert begun.revision == AgentConfig.from_workspace(project / WORKSPACE).revision
