"""Run the A/B pairs: every task × repetition, each arm in a fresh worktree with its configuration installed.

A run: a detached worktree at the task's parent commit; the arm's configuration snapshot copied in; one new Claude
Code session that sees only the project's settings (``--setting-sources project,local``: no user skills, CLAUDE.md or
memory) and may only edit files and run the commands in ``ALLOWED``; then the hidden check. Each finished run appends
one record to ``results.jsonl``, so a stopped run resumes where it stopped. The schedule is fixed by the seed: pairs
are shuffled (interleaved over time) and the order within each pair is random.

With ``graphify`` set, every session starts with the knowledge graph of its commit in ``graphify-out/``, as a project
where Graphify is set up has one: built once per commit (``graphify update .``, from the code alone, no API calls)
and copied in.

An infrastructure fault (the harness cannot start, the provider fails) scores the run ``passed: None``: the analysis
leaves the pair out, and reports how many. Anything the agent does, a timeout included, is scored by the check.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any

from .tasks import Environment, Task, check, copy_config, git

NESTING = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
# What a session may do without asking (it cannot ask: it runs unattended): edit files in its worktree, and run the
# commands a task needs. Any other command is refused, the same in both arms; no web access.
ALLOWED = ("Read", "Edit", "Write", "Glob", "Grep", "Bash(python:*)", "Bash(python3:*)", "Bash(pytest:*)",
           "Bash(graphify:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
           "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(find:*)", "Bash(grep:*)",
           "Bash(rg:*)")
# What a failed session says when the fault is the provider's, not the agent's.
LIMITS = ("hit your", "usage limit", "limit · resets", "Credit balance")  # The account's: stop, resume later.
INFRASTRUCTURE = ("API Error", "overloaded", "rate limit", "Not logged in", *LIMITS)


@dataclass(frozen=True)
class Arm:
    name: str  # "A" or "B".
    config: Path  # A folder holding the configuration files, as they sit in the project.
    files: tuple[str, ...]  # Which of them to install (e.g. ".claude", "CLAUDE.md").


@dataclass(frozen=True)
class Settings:
    repo: Path  # A clone of the project with every task's commits.
    out: Path  # results.jsonl and one folder per run.
    env: Environment
    model: str
    path: tuple[Path, ...] = ()  # Prepended to PATH in sessions: the project's Python, the configuration's tools.
    timeout: float = 1200
    repetitions: int = 3
    seed: int = 0
    jobs: int = 1
    graphify: Path | None = None  # Build each commit's graph with this binary and install it in every run.
    graphs: Path | None = None  # Where the built graphs are kept, one folder per commit.


def schedule(tasks: Sequence[Task], arms: Sequence[Arm], repetitions: int, seed: int) -> list[tuple[Task, Arm, int]]:
    """Every (task, arm, repetition), pairs shuffled and each pair's order random: reproducible from ``seed``."""
    rng = random.Random(seed)
    pairs = [(task, r) for task in tasks for r in range(repetitions)]
    rng.shuffle(pairs)
    runs = []
    for task, r in pairs:
        order = list(arms)
        rng.shuffle(order)
        runs += [(task, arm, r) for arm in order]
    return runs


def run_all(tasks: Sequence[Task], arms: Sequence[Arm], prompts: dict[str, str], settings: Settings) -> None:
    settings.out.mkdir(parents=True, exist_ok=True)
    results = settings.out / "results.jsonl"
    done = set()
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["passed"] is not None:  # An unscored run is run again; the analysis takes the last record.
                done.add((record["task"], record["arm"], record["repetition"]))
    todo = [(t, a, r) for t, a, r in schedule(tasks, arms, settings.repetitions, settings.seed)
            if (t.id, a.name, r) not in done]
    print(f"{len(done)} run(s) done, {len(todo)} to go", flush=True)
    build_graphs(settings, sorted({task.parent for task, _, _ in todo}))
    lock, limited = Lock(), Event()

    def one(job: tuple[Task, Arm, int]) -> None:
        if limited.is_set():  # Past a usage limit every run would fail the same way: leave them for a resume.
            return
        task, arm, repetition = job
        record = run_one(task, arm, repetition, prompts[task.id], settings)
        with lock, results.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        print(f"{task.id} {arm.name} #{repetition}: {_outcome(record)} ({record['seconds']:.0f}s, "
              f"${record.get('cost') or 0:.2f})", flush=True)
        if any(sign in str(record.get("fault")) for sign in LIMITS):
            limited.set()
            print(f"Stopping: {record['fault']}. Run the same command again to resume.", flush=True)

    with ThreadPoolExecutor(settings.jobs) as pool:
        list(pool.map(one, todo))


def _outcome(record: dict[str, Any]) -> str:
    return {True: "passed", False: "failed", None: f"unscored ({record.get('fault')})"}[record["passed"]]


def run_one(task: Task, arm: Arm, repetition: int, prompt: str, settings: Settings) -> dict[str, Any]:
    folder = settings.out / "runs" / f"{task.id}-{arm.name}-{repetition}"
    folder.mkdir(parents=True, exist_ok=True)
    work = settings.out / "worktrees" / folder.name
    started = time.monotonic()
    record: dict[str, Any] = {"task": task.id, "family": task.family, "arm": arm.name, "repetition": repetition,
                              "parent": task.parent, "model": settings.model,
                              "started": datetime.now(UTC).isoformat()}
    subprocess.run(["git", "-C", str(settings.repo), "worktree", "remove", "--force", str(work)],
                   capture_output=True, check=False)
    git(settings.repo, "worktree", "add", "-q", "--detach", str(work), task.parent)
    try:
        copy_config(arm.config, arm.files, work)
        install_graph(settings, task.parent, work)
        session = _session(work, prompt, folder / "transcript.jsonl", settings)
        record.update(session)
        (folder / "patch.diff").write_text(_patch(work, arm.files), encoding="utf-8")
        if session.get("fault"):
            record["passed"] = None
        else:
            checked = check(task, work, settings.repo, settings.env)
            record.update(passed=checked["passed"], score=checked["score"], fixed=checked["fixed"],
                          required=checked["required"], failed_tests=checked["failed"][:20])
    finally:
        subprocess.run(["git", "-C", str(settings.repo), "worktree", "remove", "--force", str(work)],
                       capture_output=True, check=False)
    record["seconds"] = time.monotonic() - started
    return record


GRAPH = "graphify-out"


def build_graphs(settings: Settings, commits: Sequence[str]) -> None:
    """Build the graph of each commit that has none yet, in a scratch worktree."""
    if settings.graphify is None or settings.graphs is None:
        return
    for commit in commits:
        built = settings.graphs / commit
        if (built / GRAPH / "graph.json").is_file():
            continue
        work = settings.graphs / f"{commit}.work"
        subprocess.run(["git", "-C", str(settings.repo), "worktree", "remove", "--force", str(work)],
                       capture_output=True, check=False)
        git(settings.repo, "worktree", "add", "-q", "--detach", str(work), commit)
        try:
            started = time.monotonic()
            subprocess.run([str(settings.graphify), "update", "."], cwd=work, check=True, capture_output=True)
            if not (work / GRAPH / "graph.json").is_file():
                raise RuntimeError(f"graphify built no graph for {commit}")
            shutil.rmtree(built, ignore_errors=True)
            shutil.copytree(work / GRAPH, built / GRAPH)
            print(f"graph of {commit[:9]} built ({time.monotonic() - started:.0f}s)", flush=True)
        finally:
            subprocess.run(["git", "-C", str(settings.repo), "worktree", "remove", "--force", str(work)],
                           capture_output=True, check=False)


def install_graph(settings: Settings, commit: str, work: Path) -> None:
    if settings.graphify is None or settings.graphs is None:
        return
    shutil.rmtree(work / GRAPH, ignore_errors=True)
    shutil.copytree(settings.graphs / commit / GRAPH, work / GRAPH)


def _session(work: Path, prompt: str, transcript: Path, settings: Settings) -> dict[str, Any]:
    env = {name: value for name, value in os.environ.items() if name not in NESTING}
    env["PATH"] = os.pathsep.join([*map(str, settings.path), env.get("PATH", "")])
    command = ["claude", "-p", "--model", settings.model, "--setting-sources", "project,local",
               "--permission-mode", "acceptEdits", "--allowedTools", ",".join(ALLOWED),
               "--disallowedTools", "WebFetch,WebSearch", "--output-format", "stream-json", "--verbose"]
    try:
        with transcript.open("w", encoding="utf-8") as stream:
            subprocess.run(command, cwd=work, env=env, input=prompt, stdout=stream, stderr=subprocess.PIPE,
                           text=True, timeout=settings.timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"timed_out": True, **_summary(transcript)}  # The agent's time is up: its work is checked as it is.
    except OSError as error:
        return {"fault": f"cannot start claude: {error}"}
    summary = _summary(transcript)
    if summary.get("result") is None:
        return {**summary, "fault": "the session ended without a result"}
    if any(sign in str(summary.get("message")) for sign in INFRASTRUCTURE):  # A usage limit even ends "successfully".
        return {**summary, "fault": str(summary.get("message"))[:200]}
    return summary


def _summary(transcript: Path) -> dict[str, Any]:
    """The session's ID, turns, usage, and cost, from its final ``result`` event."""
    summary: dict[str, Any] = {"result": None}
    if not transcript.is_file():
        return summary
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            summary["session"] = event.get("session_id")
        if event.get("type") == "result":
            usage = event.get("usage") or {}
            summary.update(result=event.get("subtype"), is_error=bool(event.get("is_error")),
                           message=str(event.get("result") or "")[:500], turns=event.get("num_turns"),
                           cost=event.get("total_cost_usd"),
                           usage={key: usage.get(key, 0) for key in ("input_tokens", "output_tokens",
                                                                      "cache_read_input_tokens",
                                                                      "cache_creation_input_tokens")})
    return summary


def _patch(work: Path, config: Sequence[str]) -> str:
    """The agent's changes: everything but the installed configuration and the knowledge graph."""
    git(work, "add", "-A", "--", ".", *(f":(exclude){name}" for name in [*config, GRAPH]))
    return git(work, "diff", "--cached", "HEAD")
