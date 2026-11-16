"""Benchmark tasks mined from a project's history, and their hidden checks.

A task is a merged change: the agent starts at its parent commit with the configuration under test installed, and is
asked for the change in words (``prompt.md``). The change's own tests are the hidden check. Mining keeps a task only
when its check is sound: the tests that **fail at the parent and pass at the merge** (``fail_to_pass``), twice in a
row, plus those that pass at both (``pass_to_pass``, so a fix that breaks them fails). A check passes when every one of
them passes on the agent's work; its **score** is the fraction of them that pass (the primary outcome: a change
asked for in words rarely passes every hidden test, so pass/fail alone would leave both arms near zero).

At check time the change's test files and its support files (examples under ``docs_src/``, which tests import) are
copied over the agent's work, so the agent never sees them and cannot have edited them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SOURCE = "fastapi/"  # The change must touch the library; the agent writes this part.
TESTS = "tests/"
SUPPORT = ("docs_src/",)  # Copied in with the tests: they are what tests import, not the solution.
REFUSE = ("pyproject.toml", "requirements", "uv.lock", "pdm.lock")  # A change of dependencies cannot run in one venv.


@dataclass
class Task:
    id: str
    commit: str  # The merged change.
    parent: str  # Where the agent starts.
    title: str
    family: str = ""
    split: str = ""  # "train" or "test".
    tests: list[str] = field(default_factory=list)  # Test files of the change, run by the check.
    support: list[str] = field(default_factory=list)  # Files copied in with them.
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "task.json").write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> Task:
        return cls(**json.loads((folder / "task.json").read_text(encoding="utf-8")))


def load_tasks(root: Path) -> list[Task]:
    return [Task.load(folder) for folder in sorted((root / "tasks").iterdir()) if (folder / "task.json").is_file()]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@dataclass(frozen=True)
class Environment:
    """Where tests run: the Python with the project's dependencies (but not the project itself, so every worktree
    imports its own code)."""

    python: Path
    timeout: float = 600


def run_tests(folder: Path, tests: Iterable[str], env: Environment) -> dict[str, bool]:
    """Each test's node ID → whether it passed, running ``tests`` in ``folder``. A file that fails to import yields no
    IDs: its tests count as not passed."""
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.xml"
        try:
            done = subprocess.run([str(env.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                                   f"--junitxml={report}", "-o", "junit_family=xunit2", *tests],
                                  cwd=folder, capture_output=True, text=True, timeout=env.timeout, check=False)
        except subprocess.TimeoutExpired:
            return {}
        if not report.is_file():  # pytest did not run at all: the environment is broken, not the work.
            raise RuntimeError(f"pytest did not run with {env.python}: {(done.stderr or done.stdout)[-500:]}")
        outcomes: dict[str, bool] = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            node = f"{case.get('classname')}::{case.get('name')}"
            outcomes[node] = not any(child.tag in {"failure", "error", "skipped"} for child in case)
        return outcomes


def overlay(source_repo: Path, commit: str, paths: Iterable[str], folder: Path) -> None:
    """Write ``paths`` as they are at ``commit`` into ``folder``."""
    for path in paths:
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.run(["git", "-C", str(source_repo), "show", f"{commit}:{path}"],
                                          check=True, capture_output=True).stdout)


def check(task: Task, work: Path, repo: Path, env: Environment) -> dict[str, Any]:
    """Copy the task's hidden tests and support over ``work`` (the agent's finished worktree) and run them."""
    overlay(repo, task.commit, [*task.tests, *task.support], work)
    outcomes = run_tests(work, task.tests, env)
    required = [*task.fail_to_pass, *task.pass_to_pass]
    failed = [node for node in required if not outcomes.get(node, False)]
    score = 1 - len(failed) / len(required) if required else 0.0
    broken = [node for node in task.fail_to_pass if node in failed]
    fixed = 1 - len(broken) / len(task.fail_to_pass) if task.fail_to_pass else 0.0
    return {"passed": not failed, "score": score, "fixed": fixed, "failed": failed, "required": len(required)}


class Unusable(Exception):
    """A commit that cannot become a task, and why."""


def mine(repo: Path, commit: str, env: Environment, scratch: Path) -> Task:
    """The task for ``commit``, with its check worked out and verified; ``Unusable`` if it has none."""
    parent = git(repo, "rev-parse", f"{commit}^").strip()
    commit = git(repo, "rev-parse", commit).strip()
    changed = [line.split("\t") for line in git(repo, "diff", "--name-status", parent, commit).splitlines()]
    paths = {parts[-1]: parts[0] for parts in changed}
    if any(path.startswith(REFUSE) for path in paths):
        raise Unusable("changes dependencies")
    if not any(path.startswith(SOURCE) and path.endswith(".py") for path in paths):
        raise Unusable(f"changes nothing in {SOURCE}")
    tests = sorted(p for p, status in paths.items() if p.startswith(TESTS) and p.endswith(".py") and status != "D"
                   and Path(p).name.startswith("test_"))
    if not tests:
        raise Unusable("adds or changes no test file")
    support = sorted(p for p, status in paths.items() if p.startswith(SUPPORT) and status != "D")
    before, after = scratch / "before", scratch / "after"
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(before), parent)
        git(repo, "worktree", "add", "-q", "--detach", str(after), commit)
        overlay(repo, commit, [*tests, *support], before)
        at_parent = run_tests(before, tests, env)
        at_merge = run_tests(after, tests, env)
        again = run_tests(after, tests, env)
    finally:
        for tree in (before, after):
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)],
                           capture_output=True, check=False)
    stable = {node for node, ok in at_merge.items() if ok and again.get(node)}
    fail_to_pass = sorted(node for node in stable if not at_parent.get(node, False))
    pass_to_pass = sorted(node for node in stable if at_parent.get(node, False))
    if not fail_to_pass:
        raise Unusable("no test fails before the change and passes after it")
    subject = git(repo, "log", "-1", "--format=%s", commit).strip()
    number = subject.rsplit("(#", 1)[-1].rstrip(")") if "(#" in subject else commit[:9]
    return Task(id=f"pr{number}", commit=commit, parent=parent, title=subject, tests=tests, support=support,
                fail_to_pass=fail_to_pass, pass_to_pass=pass_to_pass)


def copy_config(source: Path, files: Iterable[str], target: Path) -> None:
    """Install a configuration snapshot: ``files`` (paths relative to ``source``) copied into ``target``."""
    for name in files:
        origin, destination = source / name, target / name
        if origin.is_dir():
            shutil.copytree(origin, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
