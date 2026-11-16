# Benchmark: is an evolved release actually better?

## The question

Release `B` came out of evolving `A`. On tasks neither of them was evolved on, does an agent with `B` installed
do better than with `A`? It must be the same harness, the same model and the same settings, with only the installed
configuration changed.

Reflection ratings say why the curator changed something, not whether the change helped. Only outcomes on new tasks
can show that.

## First experiment: Graphify on FastAPI, with Claude Code

- **`A`**: [Graphify](https://github.com/Graphify-Labs/graphify) 0.9.99, installed by its own installer
  (`graphify install --project --platform claude`, `graphify claude install`) into
  [FastAPI](https://github.com/fastapi/fastapi) at `45cb2252`, then adopted by thearc
  (`thearc skills install self-evolve`), which adds only section markers. Its files (`.claude/`, `CLAUDE.md`, minus
  thearc's own evolve skill) are the snapshot `A`.
- **`B`**: one `/evolve` round from `A`, on the sessions of the train tasks only, applied unedited, and snapshotted
  the same way.

### Tasks

Tasks are FastAPI's own recent changes (`fastapi-graphify/tasks/`), mined with `python -m benchmarking.bench mine`. A
task is a merged pull request that changes `fastapi/` and its tests: the agent starts at the parent commit, and the
change's tests are the hidden check. A task is kept only when its check is sound: tests that **fail at the parent and
pass at the merge**, twice in a row (`fail_to_pass`), plus those of its files that pass at both (`pass_to_pass`).
Changes of dependencies are left out, and so are tasks whose tests import private names that the change created,
which no agent could guess.

Each prompt follows one rule (`bench/prompts.py`), because the pull requests' own descriptions are empty: the change's
title as a request, and its shortest flipping test as the example that should pass. The other hidden tests stay
hidden.

Related changes (a series of pull requests on one feature) are one **family**, and every family is in one split:

- **train**: 15 tasks in 3 families (`AliasChoices`, streaming, OpenTelemetry). Only their sessions are evolved on.
- **test**: 46 tasks in 25 families. They are used only in the comparison.

### Runs

`python -m benchmarking.bench run` takes every test task × 3 repetitions. Each repetition is a pair, `A` and `B` in a
random order, and the pairs are interleaved, so provider drift hits both alike. A run gets a fresh worktree at the
task's parent commit with the arm's snapshot copied in, then one new Claude Code session:

- it starts with the knowledge graph of its commit in `graphify-out/`, as a project with Graphify set up has one
  (built once per commit with `graphify update .`, from the code alone, and copied in);
- it sees only the project's settings (`--setting-sources project,local`), so no user skills, `CLAUDE.md` or memory;
- it can edit files and run only the commands a task needs (Python, pytest, `graphify`, read-only git and file
  commands), with no web access;
- the same model, time limit and `PATH` apply in both arms.

Then the hidden tests are copied in and run. Each run appends a record to `results.jsonl`, with its transcript and
patch kept beside it. A timeout is scored like any other work. A provider or runner fault leaves the run unscored, and
its pair is rerun.

## Is the difference real?

The outcome of a run is **fixed**: the share of the change's failing tests (`fail_to_pass`) that pass. Two more are
reported. Whether every hidden test passes ("resolved") is too coarse alone: a change asked for in words rarely passes
them all. The share of *all* hidden tests passing ("score") is diluted by `pass_to_pass` tests, which pass whatever
the agent does, so it sits near the ceiling in both arms.

For each task, `d` is `B`'s mean score minus `A`'s over its repetitions. `d` is averaged within each family, then
across families: that average is the difference, `Delta`.

Families are the independent samples: repetitions of one task are not new evidence, and neither are related
changes. So the uncertainty comes from resampling **families**: they are drawn with replacement 10,000 times, each
with all its tasks and repetitions, and the interval is the 2.5th–97.5th percentile of `Delta`. With 20 families or
fewer, a sign-flip test on the family averages must agree.

- **Improvement:** the whole interval is above 0.
- **Regression:** the whole interval is below 0.
- **No visible difference:** the interval contains 0. Report this as it is, not as a small improvement.

The tasks, repetitions and limits are fixed before any result is seen. Nothing is added or rerun after.

**How many families?** If family averages vary by about `sd = 0.3`, seeing a 10-point improvement 80% of the time
needs about `(2.8 · 0.3 / 0.10)² ≈ 71` families, and 25 families can only show a gain of about 17 points or more.
More repetitions reduce noise within a task, but they do not replace families. `analyze` prints the measured `sd`,
and with it how many families a convincing result needs.

`python -m benchmarking.bench analyze` also reports each arm's mean outcomes, how many runs were resolved, cost, time
and turns. Transcripts where `A` and `B` differ show *why*.

## Running it

```sh
B=artifacts/benchmark
G="--graphify $B/.venv-graphify/bin/graphify --graphs $B/graphs"
python -m benchmarking.bench train --benchmark benchmarking/fastapi-graphify --project $B/evolve-project \
  --model claude-sonnet-5-5 --path $B/.venv-fastapi/bin --path $B/.venv-graphify/bin $G --out $B/train
(cd $B/evolve-project && thearc evolve && thearc evolve apply)
python -m benchmarking.bench snapshot --project $B/evolve-project --out $B/arms/B
python -m benchmarking.bench run --benchmark benchmarking/fastapi-graphify --repo $B/fastapi \
  --python $B/.venv-fastapi/bin/python --arm A=$B/arms/A --arm B=$B/arms/B --model claude-sonnet-5-5 \
  --path $B/.venv-fastapi/bin --path $B/.venv-graphify/bin $G --out $B/runs/2
python -m benchmarking.bench analyze --out $B/runs/2
```

`artifacts/benchmark/` holds the FastAPI clone, the two virtual environments (FastAPI's test dependencies without
FastAPI itself, so each worktree imports its own code; and Graphify 0.9.99), the project being evolved, the
built graphs, and the snapshots. It is local and not tracked.

## Results

**Run 1 (no graph), not a test of Graphify.** No session had a graph: `graphify-out/` is not committed, and nothing
built it. The evolve round learned exactly that, and `B` only said "check whether the graph exists, and skip Graphify
if not". In the test, 68 of 138 `A` sessions ran `graphify query` and every call failed. Score `A` 89.2%, `B` 89.6%
(Delta +0.1%, interval −1.0% to +1.2%); resolved 64/138 against 71/138 (Delta +3.4%, interval −3.0% to +10.2%): no
visible difference. Skipping the failed calls did not measurably help. Run 2 builds the graph and evolves anew.

## Later

Codex comes next, as a separate experiment with its own result. Whether evolution *in general* helps, beyond this one
`B`, needs several independent rounds from the same `A`, each from its own train sessions; that comes after this one
shows anything.
