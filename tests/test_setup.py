"""``honed setup`` and the global reflect skill: one command from a plain folder to a committed /evolve setup."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from honed.cli import main
from honed.evolve import setup
from honed.resources import install


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def identity(monkeypatch, tmp_path):
    """A git identity and an isolated global git config, so commits work anywhere."""
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))


def test_setup_initializes_git_detects_the_harness_and_commits_only_what_it_installed(agent, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    install(agent, "claude", root)  # A hand-made Claude configuration: .claude/ and CLAUDE.md.
    (root / "notes.txt").write_text("mine\n")
    done = setup.setup_project(root)
    assert done.git_initialized and done.setup.harness == "claude" and done.commit
    committed = set(git(root, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert {".honed/installed.json", ".gitignore",
            ".claude/skills/evolve/SKILL.md", ".claude/skills/reflect/SKILL.md"} <= committed
    assert ".honed/workspace" not in committed and done.remote is None  # Local: out of the project.
    assert "notes.txt" not in committed and git(root, "status", "--porcelain", "notes.txt") == "?? notes.txt"

    git(root, "add", "notes.txt")  # Staged by the user: a rerun commits nothing of theirs.
    again = setup.setup_project(root)
    assert not again.git_initialized and again.commit is None
    assert git(root, "status", "--porcelain", "notes.txt") == "A  notes.txt"


def test_setup_needs_a_harness_it_can_tell(tmp_path, monkeypatch):
    for mark in (".claude", ".codex"):
        (tmp_path / mark).mkdir()
    with pytest.raises(ValueError, match="claude, codex; pick one with --harness"):
        setup.detect_harness(tmp_path)
    (tmp_path / ".codex").rmdir()
    assert setup.detect_harness(tmp_path) == "claude"
    (tmp_path / ".claude").rmdir()
    monkeypatch.setattr(setup.shutil, "which", lambda command: "/bin/pi" if command == "pi" else None)
    assert setup.detect_harness(tmp_path) == "pi"  # Nothing in the folder: the one on PATH.


def test_setup_never_inits_the_home_folder_and_honors_no_git_init(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="not a project folder"):
        setup.ensure_repository(tmp_path)
    with pytest.raises(ValueError, match="git init"):
        setup.ensure_repository(tmp_path / "app", init=False)


def test_the_cli_sets_up_and_installs_reflect_globally(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["setup", str(tmp_path / "app"), "--harness", "claude", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["git_initialized"] is True

    result = runner.invoke(main, ["skills", "install", "reflect", "--global", "--harness", "claude"])
    assert result.exit_code == 0, result.output
    skill = tmp_path / "claude-home/skills/reflect/SKILL.md"
    assert "--harness claude" in skill.read_text()
    assert runner.invoke(main, ["skills", "install", "reflect", "--global", "--harness", "claude"]).exit_code == 0
    result = runner.invoke(main, ["skills", "uninstall", "reflect", "--global"])
    assert "Removed /reflect for claude." in result.output and not skill.exists()


def test_setup_moves_a_thearc_project_to_honed(tmp_path):
    root = tmp_path / "app"
    assert setup.setup_project(root, harness="claude").commit
    git(root, "mv", ".honed", ".thearc")  # As thearc left it: tracked .thearc, thearc-* agents, its .gitignore line.
    (root / ".thearc/cache").mkdir(exist_ok=True)
    (root / ".thearc/cache/note").write_text("local\n")
    record = root / ".thearc/installed.json"
    record.write_text(record.read_text().replace('"honed-evolve"', '"thearc-evolve"'))
    (root / ".gitignore").write_text("/.thearc/cache/\n/.thearc/workspace/\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "thearc")
    with pytest.raises(ValueError, match="run `honed setup`"):
        setup.project_root(root)

    done = setup.setup_project(root, harness="claude")
    assert done.migrated and done.commit and not (root / ".thearc").exists()
    assert (root / ".honed/cache/note").read_text() == "local\n"
    assert "honed-evolve" in json.loads((root / ".honed/installed.json").read_text())["agents"]
    assert (root / ".gitignore").read_text() == "/.honed/cache/\n/.honed/workspace/\n"
    assert not git(root, "ls-files", ".thearc") and git(root, "status", "--porcelain") == ""


def test_setup_commits_around_what_gitignore_ignores(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / ".gitignore").write_text(".claude/\n")
    done = setup.setup_project(root, harness="claude")
    assert done.commit and ".claude/skills/evolve/SKILL.md" in done.ignored
    assert (root / ".claude/skills/evolve/SKILL.md").is_file()  # Installed all the same.
    assert ".honed/installed.json" in git(root, "show", "--name-only", "--format=", "HEAD")


def test_setup_with_a_remote_pushes_the_workspace_and_commits_the_submodule(tmp_path, monkeypatch):
    for key, value in (("COUNT", "1"), ("KEY_0", "protocol.file.allow"), ("VALUE_0", "always")):
        monkeypatch.setenv(f"GIT_CONFIG_{key}", value)
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    root = tmp_path / "app"
    done = setup.setup_project(root, harness="claude", remote=str(shared))
    assert done.remote == str(shared) and git(shared, "rev-parse", "main")
    committed = set(git(root, "show", "--name-only", "--format=", "HEAD").splitlines())
    assert {".gitmodules", ".honed/workspace"} <= committed


def test_create_remote_names_the_repository_after_the_projects_github_owner(tmp_path, monkeypatch):
    root = tmp_path / "app"
    setup.ensure_repository(root)
    git(root, "remote", "add", "origin", "git@github.com:acme/app.git")
    calls = []
    run = subprocess.run

    def gh(args, **kwargs):  # Fakes gh; git runs for real.
        if args[0] != "gh":
            return run(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(setup.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(setup.subprocess, "run", gh)
    assert setup.github_suggestion(root) == "acme/app-honed"
    assert setup.create_github_remote(root, "acme/app-honed") == "git@github.com:acme/app-honed.git"
    assert calls[-1][:5] == ["gh", "repo", "create", "acme/app-honed", "--private"]


def test_setup_asks_where_the_workspace_goes(tmp_path, monkeypatch):
    for key, value in (("COUNT", "1"), ("KEY_0", "protocol.file.allow"), ("VALUE_0", "always")):
        monkeypatch.setenv(f"GIT_CONFIG_{key}", value)
    from honed.evolve import cli

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(setup.shutil, "which", lambda command: None if command == "gh" else f"/usr/bin/{command}")
    shared = tmp_path / "workspace.git"
    git(tmp_path, "init", "-q", "--bare", str(shared))
    result = CliRunner().invoke(main, ["setup", str(tmp_path / "app"), "--harness", "claude"],
                                input=f"1\n{shared}\n")  # No gh: [1] a URL, [2] local.
    assert result.exit_code == 0, result.output
    assert "[1] use a repository URL" in result.output and git(shared, "rev-parse", "main")

    result = CliRunner().invoke(main, ["setup", str(tmp_path / "app"), "--harness", "claude"])
    assert "Workspace remote" not in result.output  # It has one: not asked again.


def test_a_recipe_declares_its_schema_as_the_model_itself(tmp_path):
    from honed.evolve import live

    root = tmp_path / "app"
    setup.setup_project(root, harness="claude", commit=False)
    recipe = root / ".claude/skills/evolve/evolve.py"
    recipe.write_text(recipe.read_text() + '''

from typing import Annotated

from pydantic import BaseModel
from honed.resources import Ref


class MyRated(BaseModel):
    ref: Ref
    usefulness: Annotated[str, USEFULNESS]


class MyLesson(BaseModel):
    summary: str
    ratings: list[MyRated]


SCHEMA = MyLesson  # No ReflectionSchema(...) around it.
''')
    begun = live.begin(root, harness="claude")
    assert "summary" in begun.schema["properties"] and "root_cause_analysis" not in json.dumps(begun.schema)
