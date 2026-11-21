"""What thearc, as honed was called before 0.4, left behind keeps working, or says how to move it."""

import json
import subprocess

import pytest

from honed.evolve.setup import project_root
from honed.resources.git import branch_prefix
from honed.sessions import default_index
from honed.sessions.export import export_events


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of the test's own, with neither index variable set."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.delenv("HONED_INDEX", raising=False)
    monkeypatch.delenv("THEARC_INDEX", raising=False)
    return folder


def test_the_index_variables_honed_first(home, monkeypatch):
    monkeypatch.setenv("THEARC_INDEX", "/old.sqlite")
    assert str(default_index()) == "/old.sqlite"
    monkeypatch.setenv("HONED_INDEX", "/new.sqlite")
    assert str(default_index()) == "/new.sqlite"


def test_the_index_thearc_made_stays_in_use_until_one_of_honed_exists(home):
    assert default_index().expanduser() == home / ".honed/learning.sqlite"
    (home / ".thearc").mkdir()
    (home / ".thearc/learning.sqlite").touch()
    assert default_index().expanduser() == home / ".thearc/learning.sqlite"
    (home / ".honed").mkdir()
    (home / ".honed/learning.sqlite").touch()
    assert default_index().expanduser() == home / ".honed/learning.sqlite"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_a_project_thearc_set_up_is_refused_with_how_to_move_it(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".thearc").mkdir()
    with pytest.raises(ValueError, match=r"git mv \.thearc \.honed"):
        project_root(tmp_path)
    (tmp_path / ".honed").mkdir()
    assert project_root(tmp_path) == tmp_path.resolve()


def test_a_workspace_thearc_made_keeps_its_branch_prefix(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "agent.json").write_text(json.dumps({"name": "claude"}))
    assert branch_prefix(tmp_path) == ""
    git(tmp_path, "config", "thearc.claude.prefix", "thearc/claude/")
    assert branch_prefix(tmp_path) == "thearc/claude/"
    git(tmp_path, "config", "honed.claude.prefix", "honed/claude/")
    assert branch_prefix(tmp_path) == "honed/claude/"


def test_the_native_export_format_answers_to_its_old_name():
    assert export_events([], "thearc").format == "honed"
