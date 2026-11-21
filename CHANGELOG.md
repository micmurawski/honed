# Changelog

Notable changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/).

## 0.4.0

### Added

- `honed setup` (`hnd su`) sets a project up for `/evolve` in one command: `git init` when the folder is not a
  repository yet (never in the home folder; `--no-git-init` refuses instead), the harness detected from the project's
  files or the one agent on PATH (`--harness` otherwise), the evolve and reflect skills installed, and one commit of
  only what it installed (`--no-commit` leaves it to you; paths `.gitignore` ignores are listed, not committed). The
  harness is its own runtime. Safe to run again.
- `honed setup` asks where the workspace goes, in a terminal: a private GitHub repository it creates with `gh`
  (`--create-remote [OWNER/NAME]`, default `<owner>/<project>-honed`), a repository URL (`--remote URL`), or this
  machine only (`--local`).
- `honed skills install reflect --global` (and `uninstall reflect --global`) installs `/reflect` for every project,
  into the harness's own folder (`~/.claude/skills`, `$CODEX_HOME/skills`, `~/.pi/agent/skills`,
  `~/.config/opencode/skills`).

### Changed

- A workspace without a remote stays on this machine, in `.gitignore`, instead of becoming a submodule whose URL
  (`./.honed/workspace`) no clone could fetch. `honed evolve remote URL` pushes the workspace (`main` and its
  releases) and adds it to the project as a submodule with that URL. A project set up earlier without a remote keeps
  its placeholder submodule until you run `honed evolve remote URL`.
- `honed evolve apply` pushes the release when the workspace has a remote (`--no-push` holds it back); it pushed only
  with `--push`.
- The quickstart (`examples/quickstart/run.sh`) installs honed from the checkout with pip when it is missing, and sets
  its project up with `honed setup`.
- thearc is renamed **honed**: the package (`pip install honed`), the module (`import honed`), the command (`honed`,
  and `hnd` for short), the repository (`micmurawski/honed`) and the docs (`micmurawski.github.io/honed`). In
  projects and in your home folder `.thearc/` becomes `.honed/`, `$THEARC_INDEX` becomes `$HONED_INDEX`, and
  `$THEARC_JUDGE_MODEL` becomes `$HONED_JUDGE_MODEL`.
- Reflectors take the output model itself where they took a `ReflectionSchema`: `Reflector.reflect`,
  `reflect_with_tools`, `handoff`, `WorkGraphReflector`, `Reflection.from_dict` and `Reflection.load` wrap it with
  the new `ReflectionSchema.of`, so `handoff(prompt, session, agent, Lesson)` is enough. A `ReflectionSchema` still
  works as before.

### Migrating from thearc

- The session index thearc made stays in use: `$THEARC_INDEX` is read when `$HONED_INDEX` is unset, and
  `~/.thearc/learning.sqlite` while `~/.honed/learning.sqlite` does not exist. Move it with
  `mv ~/.thearc ~/.honed` when you like.
- A project thearc set up is moved by `honed setup`: `.thearc` becomes `.honed` (`git mv` for what git tracks, the
  local cache too), the install record's `thearc-*` agents become `honed-*`, `.gitignore` follows, and the skills are
  reinstalled (the installed `evolve.py` imported `thearc`). Other commands refuse such a project until then.
- Workspaces thearc made keep their branch names: the prefix is still read from `thearc.<agent>.prefix` in git
  config.
- `--format thearc` still exports sessions, as `honed`.

## 0.3.0

### Changed

- `pip install thearc` is the whole install: `mcp` (the tools reflectors, curators, and mergers hand their work in
  through) and `numpy` (session archetypes) are dependencies. The `mcp` and `aeg` extras remain, empty, so existing
  install commands keep working. thearc is installed from PyPI in the README and the docs.
- Codex reflectors, curators, and mergers run through the installed `codex` CLI (`codex exec --json`, `exec fork` to
  hand off a session, `exec resume` for later turns), like Claude Code and Pi, instead of the `openai-codex` SDK and
  the Codex binary it bundled. The `codex` extra is removed.
- `MetaAgent` is renamed `AgentConfig`: it is an agent's configuration, not an agent. `MetaAgent` still imports, as a
  deprecated alias that warns, until the next minor release.

### Fixed

- A handed-off Codex session's usage no longer includes the usage of the session it was forked from.

## 0.2.0

### Added

- In-session reflection: the reflect skill (`/reflect`), installed with `self-evolve` beside `/evolve`, lets the
  session's own agent reflect at a milestone, as a turn of the live session (its history read from the prompt cache).
  `thearc reflect begin|submit` (`--no-reflect` to leave the skill out). `thearc skills install reflect --harness H …` installs it
  on its own for any harness (Claude Code, Codex, Pi, OpenCode, Antigravity): it then judges the configuration
  installed for that harness, with no evolution workspace.
- Every reflection is kept in the session index (`$THEARC_INDEX`, else `~/.thearc/learning.sqlite`), beside the
  session it judged: prompt name,
  version and hash, schema hash, mode, segment, ratings, and coverage per question. `thearc sessions query` finds
  sessions by their reflections (`-reflected:yes`, `-prompt:reflect`, `NOT (prompt:reflect@1 coverage:full)`) and
  lists reflections (`select:reflection`).
- The default `/evolve` recipe partitions the window's sessions into covered, partly covered and not reflected on,
  takes the reflections made already (`SOURCE`, `SOURCE_MATCH`, `SOURCE_MODES`), and reflects only on the rest and on
  long uncovered tails (`TAILS`, `TAIL_MIN_EVENTS`). Prompts carry `id` and `version` in their frontmatter.
- A round reuses the reflections of an earlier one on the same session, revision, and question (a discarded proposal,
  or a round that changed nothing) instead of paying for them again; the estimate counts them apart.
- Redaction: secrets and home directories are taken out of a session before a reflector reads it
  (`Reflector(redaction=…)`, on by default); what was taken out is listed in the reflection.
- `thearc evolve --estimate` and `/evolve estimate`: a round's sessions, their size, and its cost from earlier rounds,
  before any model call. The recipe logs the estimate and stops over its `BUDGET`.
- Codex turns time out like Claude Code's and Pi's; the recipe's `TIMEOUT` bounds every reflection and curation turn.
- `examples/quickstart`: a toy project, three Claude Code sessions, and one `/evolve` round, end to end.
- Token usage of every reflector, curator, and merger run (and cost, where the harness reports it), recorded in
  the journal and shown in the evolution report's **Token usage** tab.
- `thearc agent install` / `uninstall`: install a MetaAgent from its workspace repository, at a release, into a
  project or a harness's own folder (`--global`).
- `/evolve`: a skill that reflects on a project's recent sessions, curates, and proposes a new version of its
  configuration (`thearc skills install self-evolve`).
- Evolution report: section charts by round, version, or score; rating scales with their own order and colours.
- Apache 2.0 license.

### Changed

- `thearc sessions search` is removed; use `thearc sessions query` (the Python `Sessions.search` stays).
- `thearc sessions trace` and `thearc sessions family` are removed; use the query field `family:<reference>` (`thearc sessions query 'family:abc123 select:thread'`, `… action:agent` for its delegation calls).
- `thearc sessions add` is folded into `sync` (`sync PATH…` registers and reads; `sync` alone reads what changed), whose report lists the registered sources (`sources` is removed). `list` is removed: use `query 'select:session'`. `install` takes references or piped query results instead of `--all`, `--harness`, `--since` and `--until`.
- Session index schema 3 (reflections); a schema-2 index is upgraded in place, keeping its events.
- Hooks another installed agent owns (e.g. `thearc-evolve`'s) are no longer imported into a project's agent as hand
  edits, and thearc's own tool agents (`thearc-evolve`, `thearc-reflect`) are never taken for the project's agent
  when its installation is imported.
- The evolve skill's sources moved to `thearc/evolve/skills/evolve/`.
- The glossary is now the [Concepts](docs/concepts.md) page of the docs.
- A round goes on without a reflection that failed, instead of stopping.

### Removed

- Training session logs from the repository; they stay local.

## 0.1.0

First tagged version.
