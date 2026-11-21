# Contributing

```sh
python -m pip install -e '.[dev]'
./lint.sh                 # ruff and mypy on honed/
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
| `honed.resources` | AgentConfig, resources, workspace, journal, ratings, markers, git, installation |
| `honed.sessions` | Transcript indexing and selection; `analysis/` contains graph and pattern views, `scanner/` behavioural matching, and `export/` harness conversions |
| `honed.reflection` | Reflector, harness runtimes, the MCP tool server, Markdown rendering |
| `honed.curation` | Curator and Merger |
| `honed.evolve` | The `/evolve` skill: setup, rounds, and its recipe |
| `honed.flow` | Flow tracking and its visualisation |

Session analysis APIs are available from `honed.sessions.analysis` (and the supported
`honed.sessions` package exports). The former flat imports such as `honed.sessions.workgraph` have
been removed.
