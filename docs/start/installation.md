# Install

`thearc` needs Python 3.11 or newer.

```sh
python -m pip install thearc
```

or, for the latest unreleased changes, from a clone of the repository: `python -m pip install -e .`.

| Extra | For |
| --- | --- |
| `docs` | Building this site. |
| `dev` | Tests, `ruff`, and `mypy`. |

Agents run in the harnesses you already use. Install and sign in to the ones you want as
reflector, curator, or merger — `codex`, `claude`, or `pi` — and keep them on your `PATH`. See
[runtime support](../reference/runtimes.md) for what each harness can do.

Check the command line:

```sh
thearc --help
thearc sessions --help
```
