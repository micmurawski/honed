# Cookbook

## Quickstart: one /evolve round on a toy project

`examples/quickstart/run.sh` needs nothing but thearc and Claude Code. It copies a tiny book-tracker project (a
`CLAUDE.md` with one wrong instruction, and one skill) into a new git repository, sets it up for `/evolve`, runs three
Claude Code sessions in it, then runs a round: reflections on each session, a curation, the proposal's diff, and the
[report](quickstart-report.html).

```sh
examples/quickstart/run.sh [FOLDER]      # default: a new temporary folder
```

See `examples/quickstart/README.md` for what to expect.

## Graphify pipelines

The [example evolution report](report.html) comes from `cookbook/evolve.py`: Graphify's skill and context evolved
over four rounds on the FastAPI repository. Each round ran coding tasks in Claude Code, Codex, Pi, and OpenCode,
reflected on every session, curated one candidate per harness, and merged them into a release. Its runs predate token
usage recording, so the **Token usage** tab is empty.

Runnable scripts in `cookbook/`, each a complete pipeline on the Graphify configuration in
`tests/fastapi` and the training sessions under `logs/`. They make real model calls.

| Script | What it does |
| --- | --- |
| `cookbook/live_reflect.py` | Imports the Graphify configuration (each harness's own hooks included) into a workspace, then reflects on sessions that ran Graphify with the `Lesson` schema and records the ratings. |
| `cookbook/live_ace.py` | One ACE round: the same reflections, then one curation over all of them, ending with the configuration diff. |
| `cookbook/live_ace_with_merge.py` | Parallel pipelines: each in its own worktree, reflecting on its own sessions and curating; a merger combines the candidates and the run ends with the diff from the base. |

```sh
.venv/bin/python cookbook/live_ace_with_merge.py
```

Results go under `artifacts/live-reflection/`: the workspace (`graphify/`), the session index,
saved reflections, and — for merged runs — one directory per run with its worktrees.
