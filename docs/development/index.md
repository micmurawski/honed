# Contributing

```sh
python -m pip install -e '.[mcp,codex,dev]'
./lint.sh                 # ruff and mypy on thearc/
python -m pytest -q       # tests/ ; the Graphify fixtures need the tests/fastapi submodule
```

Tests make no model calls: scripted runtimes stand in for reflectors, curators, and mergers, and
call the real MCP tools. Live checks are the cookbook scripts.

## Where things are defined

- [Concepts](../concepts.md) (`docs/concepts.md`) defines every concept — AgentConfig, journal, markers,
  reflection, curation, merging — and is updated together with the code.

## Layout

| Package | Holds |
| --- | --- |
| `thearc.resources` | AgentConfig, resources, workspace, journal, ratings, markers, git, installation |
| `thearc.sessions` | Transcript indexing and selection; `analysis/` contains graph and pattern views, `scanner/` behavioural matching, and `export/` harness conversions |
| `thearc.reflection` | Reflector, harness runtimes, the MCP tool server, Markdown rendering |
| `thearc.curation` | Curator and Merger |
| `thearc.evolve` | The `/evolve` skill: setup, rounds, and its recipe |
| `thearc.flow` | Flow tracking and its visualisation |

Session analysis APIs are available from `thearc.sessions.analysis` (and the supported
`thearc.sessions` package exports). The former flat imports such as `thearc.sessions.workgraph` have
been removed.
