# CLI

```text
honed sessions [--index PATH] COMMAND      (alias: honed s)
```

The index defaults to `$HONED_INDEX`, else `~/.honed/learning.sqlite`. It keeps sessions and the reflections on them (`/reflect`, `/evolve`): unlike the sessions, which are read again from their transcripts, reflections live only there.

| Command | Does |
| --- | --- |
| `sync [PATHS…] [--harness H] [--format native\|dataclaw] [--source-id ID]` (alias `sy`) | Register transcript directories as sources, then read new and changed transcripts. |
| `query QUERY [--limit N] [--offset N] [--json] [--fields]` (alias `q`) | Events, threads, sessions or reflections matching a query; see [Query language](query.md). |
| `show EVENT_ID [--context N]` (alias `sh`) | An event with its surrounding events. |
| `trajectories [SESSION]` (alias `t`) | A session's trajectories. |
| `render [SESSION] [--trajectory N] [--through] [--format F] [--best-effort]` (alias `r`) | Markdown, or a harness's transcript format. |
| `install [REFERENCES…] --to H [--trajectory N] [--through]` (alias `i`) | Install sessions where a harness can resume or fork them. |
| `graph COMMAND` (alias `g`) | Graph the sessions a query selected, and query the graphs (`graph query`). |

```text
honed report WORKSPACE [-o report.html] [--ref BRANCH …] [--reflections DIR]
```

Writes the [evolution report](../guides/report.md) of a workspace.

```text
honed agent install --repo URL|PATH --harness H [--version VERSION] [--project DIR | --global] [--force]
honed agent uninstall --name NAME --harness H [--project DIR | --global]
```

Installs an AgentConfig from its workspace repository (`--repo`, or its spellings `--url` and `--path`: a git URL is
cloned to a temporary folder), at the release `--version` names (its `release/<version>` branch; default `main`).
`--project` (the default, with the current folder) installs into the harness's project layout; `--global` into the
harness's own folder for every project: `~/.claude` (or `$CLAUDE_CONFIG_DIR`), `~/.codex` (or `$CODEX_HOME`),
`~/.pi/agent`, `~/.config/opencode`. The install record goes beside what was installed, so reinstalling replaces
exactly what the last install wrote. A global install for Claude skips MCP servers (Claude keeps them in
`~/.claude.json`), and hook scripts run by their absolute path. `--force` overwrites files the agent's earlier
installs did not write.

```text
honed setup [DIR] [--harness H] [--ignore PATTERN …] [--remote URL | --create-remote [OWNER/NAME] | --local]
            [--no-git-init] [--no-commit]
honed skills install self-evolve [--project DIR] --harness H [--runtime H] [--ignore PATTERN …] [--remote URL]
honed skills uninstall self-evolve [--project DIR]
honed evolve [--project DIR] [--json] [--recipe FILE] [--open] [--estimate]
honed evolve diff | amend -m WHY | apply [--push/--no-push] | discard | status | ignore PATTERN… [--remove] | rollback VERSION
honed evolve init | remote URL
```

Set a project up for [/evolve](../guides/evolve-skill.md), and run its rounds. `honed setup` does the whole setup
in one step (`git init` if needed, the harness detected, a commit of what it installed); `skills install self-evolve`
is the install alone.

```text
honed skills install reflect (--project DIR | --global) --harness H [--harness H …]
honed skills uninstall reflect [--project DIR | --global] [--harness H …]
```

`/reflect` on its own, for a project or, with `--global`, for every project (into the harness's own folder, as
`agent install --global`). `/evolve` itself is per project: its workspace and sessions belong to one project.

Run any command with `--help` for every option.
