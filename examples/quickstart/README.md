# Quickstart: one /evolve round on a toy project

`shelf` is a tiny command-line book tracker with a `CLAUDE.md` and one skill. One line of its `CLAUDE.md` is
wrong: it says to run the tests with `python3 tests/run_all.py`, which doesn't exist. The script works in the project
the way you would, then lets honed learn from those sessions:

1. copies `project/` into a new git repository and sets it up for evolution
   (`honed skills install self-evolve --harness claude`);
2. runs three Claude Code sessions in it, one per file in `tasks/`;
3. runs a round (`honed evolve`): a reflector reviews `CLAUDE.md` and the skill against each session and rates the
   sections it can judge, and a curator edits the configuration from those ratings into a proposal;
4. prints the proposal, its diff, and the path of the round's report.

```sh
examples/quickstart/run.sh            # in a new temporary folder; or give one: run.sh ~/shelf
MODEL=haiku examples/quickstart/run.sh   # the three work sessions on a cheaper model
```

It needs honed (`python -m pip install -e .` in this repository) and Claude Code, installed and signed in.
It makes real model calls: three short sessions, three reflections, and one curation: a few minutes, and about a dollar for the round on
Claude Code's default model (the report's **Token usage** tab shows what each run cost).

Expect the testing instruction to be rated harmful and rewritten to `python3 -m unittest`, and the `add-command` skill
to be rated helpful and kept. The model decides, so the details vary from run to run. The published
[evolution report](https://micmurawski.github.io/honed/examples/report.html) is a separate, larger Graphify example
with four releases and parallel candidates that are merged between releases. The quickstart prints the path to its
own report. Then:

```sh
cd <the folder it printed>
honed evolve diff       # the proposal again
honed evolve apply      # install it: the next session reads the new CLAUDE.md
open <the report it printed>   # every section, its ratings, and what changed
```

In your own project the round is the same: install the skill, work as usual, and type `/evolve` in Claude Code.
