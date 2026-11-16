"""Benchmark tasks mined from history, and their hidden checks (benchmarking/bench/tasks.py)."""

import subprocess
import sys

import pytest

from benchmarking.bench.tasks import Environment, Task, Unusable, check, mine


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(root, files, message):
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"fastapi/__init__.py": "", "tests/__init__.py": "",
                  "fastapi/maths.py": "def add(a, b):\n    return a - b\n",
                  "tests/test_old.py": "from fastapi.maths import add\n\n"
                                       "def test_zero():\n    assert add(0, 0) == 0\n"},
           "Start")
    return root


ENV = Environment(sys.executable, timeout=120)


def test_a_fix_with_its_tests_becomes_a_task_whose_check_tells_right_from_wrong(repo, tmp_path):
    fixed = commit(repo, {"fastapi/maths.py": "def add(a, b):\n    return a + b\n",
                          "tests/test_add.py": "from fastapi.maths import add\n\n"
                                               "def test_adds():\n    assert add(2, 3) == 5\n\n"
                                               "def test_zero():\n    assert add(0, 0) == 0\n"},
                   "Fix add (#7)")
    task = mine(repo, fixed, ENV, tmp_path / "scratch")
    assert (task.id, task.tests) == ("pr7", ["tests/test_add.py"])
    assert task.fail_to_pass == ["tests.test_add::test_adds"] and task.pass_to_pass == ["tests.test_add::test_zero"]
    task.save(tmp_path / "t")
    assert Task.load(tmp_path / "t") == task

    work = tmp_path / "work"
    git(repo, "worktree", "add", "-q", "--detach", str(work), task.parent)
    assert not (work / "tests/test_add.py").exists()  # Hidden from the agent.
    assert check(task, work, repo, ENV) == {"passed": False, "score": 0.5, "fixed": 0.0,
                                            "failed": ["tests.test_add::test_adds"], "required": 2}
    (work / "fastapi/maths.py").write_text("def add(a, b):\n    return 5\n")  # Passes one test, breaks the other.
    assert check(task, work, repo, ENV)["failed"] == ["tests.test_add::test_zero"]
    (work / "fastapi/maths.py").write_text("def add(a, b):\n    return b + a\n")
    (work / "tests/test_add.py").write_text("def test_adds():\n    pass\n")  # The agent's own version is replaced.
    assert check(task, work, repo, ENV)["passed"]


def test_changes_without_a_test_that_flips_are_not_tasks(repo, tmp_path):
    docs = commit(repo, {"README.md": "docs\n"}, "Docs")
    with pytest.raises(Unusable, match="nothing in fastapi/"):
        mine(repo, docs, ENV, tmp_path / "a")
    refactor = commit(repo, {"fastapi/maths.py": "def add(a, b):\n    return a - b  # same\n",
                             "tests/test_more.py": "def test_true():\n    assert True\n"}, "Refactor")
    with pytest.raises(Unusable, match="no test fails"):
        mine(repo, refactor, ENV, tmp_path / "b")
    deps = commit(repo, {"fastapi/maths.py": "x = 1\n", "pyproject.toml": "[project]\n",
                         "tests/test_x.py": "def test_x():\n    pass\n"}, "Bump")
    with pytest.raises(Unusable, match="dependencies"):
        mine(repo, deps, ENV, tmp_path / "c")


def test_a_broken_environment_stops_instead_of_scoring_nothing(repo, tmp_path):
    fixed = commit(repo, {"fastapi/maths.py": "def add(a, b):\n    return a + b\n",
                          "tests/test_add.py": "from fastapi.maths import add\n\ndef test_adds():\n"
                                               "    assert add(2, 3) == 5\n"}, "Fix")
    bare = tmp_path / "bare"  # A Python without pytest: the error the miner once took for "no test flips".
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(bare)], check=True)
    with pytest.raises(RuntimeError, match="pytest did not run"):
        mine(repo, fixed, Environment(bare / "bin" / "python"), tmp_path / "s")
