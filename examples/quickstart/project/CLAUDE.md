# shelf

A small command-line book tracker: `python3 -m shelf.cli add|list|read`. The code is in `shelf/`
(`store.py` keeps the books in `shelf.json`, `cli.py` is the command line), the tests in `tests/`.

## Testing

Run the whole suite with `python3 tests/run_all.py` before you finish.

## Style

- Standard library only; no new dependencies.
- Keep the functions in `store.py` pure: they take the books and return new ones; only `load` and `save`
  touch the file.
- Every function gets a one-line docstring.
