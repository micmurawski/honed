# Contributing to honed

Thanks for helping. Bug reports, harness support, docs fixes, and ideas are all welcome.

## Set up

```sh
git clone --recurse-submodules https://github.com/micmurawski/honed
cd honed
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev,docs]'
```

`tests/fastapi` is a submodule with the Graphify configuration the fixtures use; clone with
`--recurse-submodules` or run `git submodule update --init`.

## Check your change

```sh
./lint.sh                          # ruff and mypy
python -m pytest -q                # no model calls: scripted runtimes stand in for agents
python -m mkdocs build --strict    # if you touched docs/
```

CI runs the same on Python 3.11 and 3.13. The report page's tests need Node.js; without it they are skipped.

## Pull requests

- Keep one change per pull request, with a test that fails without it.
- Update [Concepts](docs/concepts.md) when you add or change a concept, and the guide for what users see.
- Follow the code around you: names, comment density, line length 120.
- Session transcripts can hold private data: don't commit real ones. Use small synthetic fixtures.

More detail on the layout and where things are defined: [docs/development](docs/development/index.md).

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
