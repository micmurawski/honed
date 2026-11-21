# Evolve a project with /evolve

The **evolve skill** lets the people working in a project evolve its skills and instructions for one harness from
the sessions they have already run there. Typing `/evolve` in the agent runs a **round**: honed reads the
project's recent sessions of that harness, reflects on each, and curates the configuration from the reflections
into a **proposal**, a new version that is not installed yet. You review the diff, ask the agent for amendments,
and apply it.

## Set a project up

One command, in the project's folder:

```bash
honed setup                    # or: honed setup PATH --harness claude
```

It runs `git init` when the folder is not in a git repository yet (`--no-git-init` to refuse instead), detects the
harness from the project's files (`.claude/`, `CLAUDE.md`, `.codex/`, `.pi/`, `.opencode/`) or else the only agent on
PATH (`--harness` when it can't tell), installs the skills as below, and commits only what it installed
(`--no-commit` to leave that to you). Run again, it reinstalls the skills.

`honed skills install self-evolve --project . --harness claude` is the same install without the git steps: the
project must already be a git repository, and you commit. Either way, this:

1. **Adopts** the project's configuration for that harness (for Claude: `.claude/skills/*`, `CLAUDE.md`, its hooks
   and MCP servers) as an AgentConfig named after the project, version `1.0.0`.
2. Keeps it in a **workspace** at `.honed/workspace`: a repository of its own (`main`, `release/…`). With a remote
   (`--remote URL`, or `honed setup --create-remote` for a private GitHub repository made with `gh`; `honed setup`
   asks in a terminal) its `main` is pushed there and the project holds it as a git submodule with that URL, so each
   project commit pins the workspace commit its installed files came from, and a clone fetches it. Without one it is
   local: kept out of the project (in `.gitignore`), so no clone gets a submodule it can't fetch.
3. Installs the **evolve skill** into the harness's skills folder (`.claude/skills/evolve/`): `SKILL.md`, the recipe
   `evolve.py` and its prompts in `prompts/`. It is not part of the AgentConfig (`agent.json` holds
   `"ignore": ["skill:evolve"]`), so nothing evolves it.

A project evolves one harness: installing for another harness is refused. `--runtime` picks the harness that runs
reflections and curation (default: `--harness`). Run the install again to reinstall the evolve skill.

The workspace is where new versions are made; `.claude/` (or the harness's own folders) holds the installed copy the
harness reads. Commit the installed files, `.gitignore`, `.honed/installed.json` and, with a remote, `.gitmodules`
and `.honed/workspace` (`honed setup` commits them). A teammate who clones the project with `--recurse-submodules`
has the workspace; otherwise `honed evolve init` checks it out. `honed evolve remote URL` gives a local workspace a
remote later: it pushes the workspace there and adds the submodule. `honed evolve apply` pushes each release to the
remote (`--no-push` to hold it back).

## A round

| In the agent | Command line | Does |
| --- | --- | --- |
| `/evolve` | `honed evolve` | Run the recipe and create the proposal; show what it changes. |
| `/evolve estimate` | `honed evolve --estimate` | The round's sessions, their size, and its cost from earlier rounds; no model calls. |
| `/evolve diff` | `honed evolve diff` | The proposal against the installed version, with who changed what. |
| `/evolve amend` | `honed evolve amend -m WHY` | Commit the edits made in `.honed/workspace` to the proposal. |
| `/evolve apply` | `honed evolve apply [--push]` | Release the proposal and install it; `--push` pushes the workspace. |
| `/evolve discard` | `honed evolve discard` | Drop the proposal. |
| `/evolve ignore …` | `honed evolve ignore PATTERN… [--remove]` | Leave targets out of evolution. |
| `/evolve status` | `honed evolve status` | Version, open proposal, harness, runtime, what is evolved and ignored. |
| | `honed evolve rollback VERSION` | Install an earlier release again, as a new version. |

Before the recipe runs, honed checks the installed files for **drift**: edits made by hand (or by another tool)
since the last install are committed to the workspace as your change, so a round never overwrites them.

The default recipe takes the harness's sessions run in the project in the last week (at most 20, never one that ran
`/evolve`), reflects on each against the installed version, and curates the configuration. It needs at least 3
sessions. Its reflections are kept whole in the journal, so a stopped round resumes where it stopped and the report
shows what each reflector said.

A session is paid for once. Once a proposal is applied, later rounds only read sessions run since. A round whose
proposal you discard, or that changes nothing, still keeps its reflections on its candidate branch, and the next round
reuses them instead of reflecting again. It does this when the session is unchanged (the same events), the
configuration is the same revision, and the reflection prompt and schema are the same. Edit `prompts/reflect.md` and
the sessions are reflected on afresh.

## What a round costs

Before it reflects, the recipe estimates the round (`evolution.estimate(sessions, RUNTIME)`) and logs it: how many
sessions, how many tokens of evidence they are, and what that cost in this workspace's earlier rounds. The first round
has no history, so it shows the size alone; each run's cost is recorded (see
[token usage](report.md#token-usage)), and later rounds are estimated from it. `honed evolve --estimate` stops there,
before any model call. Sessions whose reflections will be reused cost nothing and are counted apart. Set `BUDGET` in the recipe (in USD) to stop a round whose estimate is over it.

Every reflection and curation turn is bounded by `TIMEOUT` (900 seconds): a stalled API call or a harness waiting on
input fails that run instead of holding the round, and a round goes on without a reflection that failed.

## What the reflector sees

Each session is redacted before a reflector reads it: known token shapes (cloud, Git hosting, model APIs, chat), private
keys, JWTs, the values of `password=` and the like, the values of your secret-named environment variables, and home
directories become `[REDACTED:<kind>]` or `~`. What was taken out is listed in the reflection's notes. See
[redaction](reflections.md#redaction) to add your own strings or turn it off.

Applied changes take effect in the next session: a harness reads its skills when a session starts.

## Amend a proposal

While a proposal is open, `.honed/workspace` is checked out on `proposal/<version>`. Ask the agent for a
change; it edits the files there (not the installed ones) and runs `honed evolve amend -m "why"`. The amendment is
checked as a curator's commit is (the journal and `agent.json` stay as they are, new sections get their markers) and
recorded with you as its author.

## Watch a round

A round takes minutes. `honed evolve` prints `Progress: file://…/.honed/cache/progress.html` first, and `--open`
opens it (the evolve skill does both). The page shows the round as it runs: its steps as a graph (find the sessions,
reflect on each, curate), which one is running, each session's reflection as it finishes, and the round's log
(`round.log`: the sessions found, each reflection's ratings, the curator's commits) in a tab. It reloads itself until
the round ends; a copy stays in the round's folder, `.honed/cache/rounds/<round>/`.

## Change the recipe

`evolve.py` and `prompts/reflect.md` and `prompts/curate.md` in the installed evolve skill are plain files: edit them,
and the next round uses them. The recipe is a [flow](https://github.com/micmurawski/honed/tree/main/honed/flow) of
steps, run with `evolution.track(flow, shared)` so it shows on the progress page. Each step is a function decorated
with `@node`: its parameters are read from the round's shared state by name, and the dict it returns is merged back
into it; `return state, "action"` picks the next step. `validate=False` hands over the round's live objects (the
evolution, its sessions, the candidate worktree) as they are.

```python
HARNESS = "claude"  # Whose sessions are read: the harness this project evolves (set at install).
RUNTIME = "claude"  # Runs every reflection and the curation.
WINDOW = timedelta(days=7)
MIN_SESSIONS = 3
BUDGET: float | None = None  # In USD: stop before reflecting when the round is estimated to cost more.
TIMEOUT = 900  # Seconds an agent's turn may take before it counts as stalled and fails.
SCHEMA = Lesson  # What a reflection answers: a pydantic model (the default is honed.evolve.SCHEMA).


@node(validate=False)
def find_sessions(evolution: Evolution):
    """The harness's sessions run in this project in the last WINDOW; enough of them to learn from?"""
    sessions = evolution.sessions(harness=HARNESS, since=WINDOW)
    if len(sessions) < MIN_SESSIONS:
        evolution.nothing(f"{len(sessions)} session(s); {MIN_SESSIONS} needed")
        return None, "too few"
    return {"sessions": sessions}, "enough"

...


find_sessions - "too few" >> too_few
find_sessions - "enough" >> partition
partition - "stop" >> stop
partition - "go" >> source >> reflect >> curate


def propose(evolution: Evolution) -> AgentConfig | None:
    return evolution.track(Flow(start=find_sessions), {"evolution": evolution}).get("proposal")
```

- `find_sessions` takes `evolution.sessions(harness=HARNESS, since=WINDOW)` and needs at least `MIN_SESSIONS`.
- `partition` splits them by what reflections made already cover (in the sessions, by `/reflect`, or by earlier
  rounds), runs the failure rules for the reflector's hints (`FOCUS`, `SELECT`), and estimates the round: it stops
  there for `--estimate` or a round over `BUDGET`, and otherwise sets up the candidate (`evolution.candidate()`).
- `source` records the reflections made already into the candidate (`candidate.add_reflections`), without a model
  call.
- `reflect` is a batch node (`@node(batch=True, items_key="to_reflect", item="handed")`): one
  `Reflector(RUNTIME, timeout=TIMEOUT).handoff(...)` per session left, or per tail of one partly covered, recorded
  with `evolution.record(candidate, reflection)`: in the candidate, and in the session index, so a stopped round
  resumed, or the next round after a discard, takes it instead of reflecting again. A changed reflect prompt asks
  the same question until its `version` is bumped.
- `curate` runs `Curator(RUNTIME, timeout=TIMEOUT).curate(...)` on the candidate and returns it as the proposal.

The steps and the graph are defined once, at module level; `propose` only runs the flow. A step with an argument it
doesn't find in the shared state, and no default for it, fails with its name.

Each node writes to `evolution.log`. `evolution.track(flow, shared, logs={"name": path})` adds more log files as tabs.
A recipe may curate several candidates in parallel (`evolution.candidate("a")`, `evolution.candidate("b")`) and
merge them with `Merger`.

## Ignored

`honed evolve ignore skill:vendor-*` or `"context:CLAUDE.md#Private notes"` leaves those targets out of the
configuration: they are not adopted, evolved or installed, and their files stay as you wrote them. The evolve skill
is ignored this way from the start. In a context document honed owns top-level sections, so ignore a top-level
section, not a subsection of one.

Patterns are `kind:name[#section]` with `*` wildcards; a pattern names its targets' subsections too, and
`skill:<name>` its skill's files.
