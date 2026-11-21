"""python -m benchmarking.bench mine|run|analyze — see benchmarking/README.md."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from .tasks import Environment, Task, Unusable, git, load_tasks, mine


def mine_command(args: argparse.Namespace) -> None:
    repo, root = args.repo.resolve(), args.benchmark.resolve()
    env = Environment(args.python.absolute())
    commits = args.commits or git(repo, "log", "--first-parent", f"--since={args.since}", "--format=%H", args.head,
                                  "--", "fastapi/").split()
    known = {Task.load(folder).commit for folder in (root / "tasks").glob("*") if (folder / "task.json").is_file()}
    for commit in commits:
        full = git(repo, "rev-parse", commit).strip()
        if full in known:
            continue
        subject = git(repo, "log", "-1", "--format=%s", full).strip()
        with tempfile.TemporaryDirectory(dir=repo.parent) as scratch:
            try:
                task = mine(repo, full, env, Path(scratch))
            except Unusable as reason:
                print(f"skip {full[:9]} {subject[:70]}: {reason}", flush=True)
                continue
        task.save(root / "tasks" / task.id)
        print(f"task {task.id} {len(task.fail_to_pass)} fail→pass, {len(task.pass_to_pass)} pass→pass: {subject[:70]}",
              flush=True)


def _arm(text: str) -> tuple[str, Path]:
    name, _, folder = text.partition("=")
    if name not in {"A", "B"} or not folder:
        raise argparse.ArgumentTypeError("an arm is A=FOLDER or B=FOLDER")
    return name, Path(folder).resolve()


def _digest(folder: Path, files: list[str]) -> str:
    """A hash of a configuration snapshot: every file under ``files``, by path and content."""
    digest = hashlib.sha256()
    for name in files:
        root = folder / name
        for path in sorted([root] if root.is_file() else root.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(folder)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()[:16]


def run_command(args: argparse.Namespace) -> None:
    from .runner import Arm, Settings, run_all

    root, repo = args.benchmark.resolve(), args.repo.resolve()
    tasks = [task for task in load_tasks(root) if task.split == args.split and (not args.only or task.id in args.only)]
    prompts = {task.id: (root / "tasks" / task.id / "prompt.md").read_text(encoding="utf-8") for task in tasks
               if (root / "tasks" / task.id / "prompt.md").is_file()}
    missing = [task.id for task in tasks if task.id not in prompts or not task.family]
    if missing:
        raise SystemExit(f"tasks without a prompt.md or a family: {', '.join(missing)}")
    if not tasks:
        raise SystemExit(f"no {args.split} tasks")
    arms = [Arm(name, folder, tuple(args.files)) for name, folder in sorted(dict(args.arm).items())]
    if [arm.name for arm in arms] != ["A", "B"]:
        raise SystemExit("give both arms: --arm A=FOLDER --arm B=FOLDER")
    plan = {"tasks": {task.id: [task.commit, hashlib.sha256(prompts[task.id].encode()).hexdigest()[:16]]
                      for task in tasks},
            "arms": {arm.name: _digest(arm.config, args.files) for arm in arms}, "files": args.files,
            "model": args.model, "repetitions": args.repetitions, "seed": args.seed, "timeout": args.timeout,
            "graph": args.graphify is not None}
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    saved = out / "plan.json"
    if saved.is_file() and json.loads(saved.read_text()) != plan:
        raise SystemExit(f"{out} holds a run with another plan; use another --out")
    saved.write_text(json.dumps(plan, indent=2) + "\n")
    settings = Settings(repo=repo, out=out, env=Environment(args.python.absolute()), model=args.model,
                        path=tuple(p.resolve() for p in args.path), timeout=args.timeout,
                        repetitions=args.repetitions, seed=args.seed, jobs=args.jobs, **_graphs(args, repo))
    run_all(tasks, arms, prompts, settings)


def _graphs(args: argparse.Namespace, repo: Path) -> dict[str, Path | None]:
    if args.graphify is None:
        return {"graphify": None, "graphs": None}
    return {"graphify": args.graphify.absolute(), "graphs": (args.graphs or repo.parent / "graphs").resolve()}


CODE = ("fastapi", "tests", "docs_src")  # What a train task resets to its parent; the configuration stays.


def train_command(args: argparse.Namespace) -> None:
    """Run the train tasks in the project being evolved, one session each, with its code at the task's parent commit
    and its installed configuration as it is. The sessions are recorded where /evolve looks for them."""
    import subprocess

    from .runner import GRAPH, Settings, _session, build_graphs, install_graph

    root, project = args.benchmark.resolve(), args.project.resolve()
    tasks = [task for task in load_tasks(root) if task.split == "train"]
    settings = Settings(repo=project, out=args.out.resolve(), env=Environment(Path("python")), model=args.model,
                        path=tuple(p.resolve() for p in args.path), timeout=args.timeout, **_graphs(args, project))
    settings.out.mkdir(parents=True, exist_ok=True)
    build_graphs(settings, sorted({task.parent for task in tasks}))
    for task in tasks:
        transcript = settings.out / f"{task.id}.jsonl"
        if transcript.is_file() and '"type":"result"' in transcript.read_text().replace(" ", ""):
            print(f"{task.id}: done before", flush=True)
            continue
        if git(project, "status", "--porcelain", "--untracked-files=no").strip():
            raise SystemExit(f"{project} has uncommitted changes; commit or drop them first")
        git(project, "rm", "-rq", "--cached", "--ignore-unmatch", *CODE)
        subprocess.run(["rm", "-rf", *CODE, GRAPH], cwd=project, check=True)
        git(project, "checkout", task.parent, "--", *CODE)
        install_graph(settings, task.parent, project)
        try:
            prompt = (root / "tasks" / task.id / "prompt.md").read_text(encoding="utf-8")
            summary = _session(project, prompt, transcript, settings)
            print(f"{task.id}: {summary.get('result')} ({summary.get('turns')} turns, ${summary.get('cost') or 0:.2f})",
                  flush=True)
        finally:  # Back to the project as it was: the code at HEAD, nothing the agent left behind.
            git(project, "reset", "-q", "--hard", "HEAD")
            git(project, "clean", "-fdq")
            subprocess.run(["rm", "-rf", GRAPH], cwd=project, check=True)


def snapshot_command(args: argparse.Namespace) -> None:
    """Copy a project's installed configuration (``--files``, minus ``--exclude``) into a snapshot folder for an arm."""
    import shutil

    project, out = args.project.resolve(), args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    for name in args.files:
        source = project / name
        if source.is_dir():
            shutil.copytree(source, out / name, ignore=shutil.ignore_patterns(*args.exclude))
        elif source.is_file():
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, out / name)
    print(f"{out}: {_digest(out, args.files)}")


def analyze_command(args: argparse.Namespace) -> None:
    from statistics import fmean

    from .analysis import analyze, families_needed, spread

    latest = {}  # A run that was rerun (unscored first) counts once: its last record.
    for line in (args.out / "results.jsonl").read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            latest[(record["task"], record["arm"], record["repetition"])] = record
    records = list(latest.values())
    unscored = sum(record["passed"] is None for record in records)
    scored = [record for record in records if record["passed"] is not None]
    primary = "fixed" if all("fixed" in record for record in scored) else "score"  # Runs before "fixed" existed.
    if primary == "fixed":
        print(f"Fixed (share of the change's failing tests passing): "
              f"{analyze(records, seed=args.seed, outcome='fixed')}")
    print(f"Score (share of all hidden tests passing): {analyze(records, seed=args.seed)}")
    print(f"Resolved (every hidden test passing): {analyze(records, seed=args.seed, outcome='passed')}")
    if unscored:
        print(f"{unscored} unscored run(s); worst case for B: "
              f"{analyze(records, seed=args.seed, worst_case=True, outcome=primary)}")
    for arm in ("A", "B"):
        mine_ = [r for r in records if r["arm"] == arm and r["passed"] is not None]
        if mine_:
            fixed = f"fixed {fmean(r['fixed'] for r in mine_):.1%}, " if primary == "fixed" else ""
            print(f"{arm}: {fixed}mean score {fmean(r.get('score', r['passed']) for r in mine_):.1%}, "
                  f"{sum(r['passed'] for r in mine_)}/{len(mine_)} resolved, "
                  f"${sum(r.get('cost') or 0 for r in mine_):.2f}, "
                  f"{fmean(r['seconds'] for r in mine_) / 60:.1f} min and "
                  f"{fmean(r.get('turns') or 0 for r in mine_):.0f} turns a run")
    sd = spread(records, outcome=primary)
    print(f"Spread of family differences ({primary}): sd = {sd:.2f}. Families for 80% power at +10 points: "
          f"{families_needed(sd, 0.10) if sd > 0 else 'n/a'}; at +20 points: "
          f"{families_needed(sd, 0.20) if sd > 0 else 'n/a'}")


def _graph_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--graphify", type=Path, help="Start every session with its commit's graph, built with this.")
    parser.add_argument("--graphs", type=Path, help="Where built graphs are kept (default: graphs/ beside the repo).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarking.bench")
    commands = parser.add_subparsers(dest="command", required=True)
    mining = commands.add_parser("mine", help="Turn merged changes into tasks with verified hidden checks.")
    mining.add_argument("--repo", type=Path, required=True, help="A clone of the project.")
    mining.add_argument("--benchmark", type=Path, required=True, help="The benchmark folder (tasks/ goes in it).")
    mining.add_argument("--python", type=Path, required=True, help="Python with the project's test dependencies.")
    mining.add_argument("--since", default="2025-10-01")
    mining.add_argument("--head", default="HEAD")
    mining.add_argument("commits", nargs="*", help="Commits to mine (default: every change to fastapi/ since --since).")
    mining.set_defaults(handler=mine_command)
    running = commands.add_parser("run", help="Run every task of a split with both arms, in pairs.")
    running.add_argument("--benchmark", type=Path, required=True)
    running.add_argument("--repo", type=Path, required=True)
    running.add_argument("--python", type=Path, required=True)
    running.add_argument("--arm", type=_arm, action="append", required=True, help="A=FOLDER or B=FOLDER")
    running.add_argument("--files", nargs="+", default=[".claude", "CLAUDE.md"], help="Configuration files to install.")
    running.add_argument("--model", required=True, help="A pinned model ID, not an alias.")
    running.add_argument("--path", type=Path, action="append", default=[], help="Prepended to sessions' PATH.")
    running.add_argument("--split", default="test")
    running.add_argument("--only", nargs="*", help="Only these task IDs (a smoke run).")
    running.add_argument("--repetitions", type=int, default=3)
    running.add_argument("--seed", type=int, default=0)
    running.add_argument("--timeout", type=float, default=1200, help="Seconds a session may take.")
    running.add_argument("--jobs", type=int, default=1, help="Runs at a time.")
    running.add_argument("--out", type=Path, required=True)
    _graph_options(running)
    running.set_defaults(handler=run_command)
    training = commands.add_parser("train", help="Run the train tasks in the project being evolved.")
    training.add_argument("--benchmark", type=Path, required=True)
    training.add_argument("--project", type=Path, required=True, help="The project set up for /evolve.")
    training.add_argument("--model", required=True)
    training.add_argument("--path", type=Path, action="append", default=[])
    training.add_argument("--timeout", type=float, default=1200)
    training.add_argument("--out", type=Path, required=True, help="Where the train transcripts go.")
    _graph_options(training)
    training.set_defaults(handler=train_command)
    snapping = commands.add_parser("snapshot", help="Save a project's installed configuration as an arm.")
    snapping.add_argument("--project", type=Path, required=True)
    snapping.add_argument("--files", nargs="+", default=[".claude", "CLAUDE.md"])
    snapping.add_argument("--exclude", nargs="*", default=["evolve"], help="Names left out (honed's own skill).")
    snapping.add_argument("--out", type=Path, required=True)
    snapping.set_defaults(handler=snapshot_command)
    analysing = commands.add_parser("analyze", help="Delta, its interval, the verdict, and costs.")
    analysing.add_argument("--out", type=Path, required=True)
    analysing.add_argument("--seed", type=int, default=0)
    analysing.set_defaults(handler=analyze_command)
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
