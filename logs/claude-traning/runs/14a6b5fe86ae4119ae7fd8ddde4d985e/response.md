The `graphify` command needs approval I don't have here, so I'm reading the source directly. Implementing the change:

I didn't make any changes: permission to write `fastapi/exception_handlers.py` and `tests/test_validation_error_code.py` was denied, so neither file was edited and the tests weren't run. The `graphify` command also needed approval, so I read the source directly.

Here is the change I'd make once you allow those writes.

**`fastapi/exception_handlers.py`**: replace `request_validation_exception_handler` with:
```python
def _error_code(error_type: str) -> str:
    return error_type.replace(".", "_").upper()


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = jsonable_encoder(exc.errors())
    for error in errors:
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            error["code"] = _error_code(error["type"])
    return JSONResponse(
        status_code=422,
        content={"detail": errors},
    )
```
`loc`, `msg`, `type` (and `input`/`ctx`) stay as they are; the only change is the added `code`.

**`tests/test_validation_error_code.py`** would have three tests, using an endpoint with `name: str = Body(min_length=5, embed=True)`:
- **Short body:** POST `{"name": "abc"}`, check for 422, then check that every error has a `code` that matches `[A-Z0-9_]+` and equals `type` uppercased with dots replaced by underscores.
- **Exact values:** check that `loc == ["body", "name"]`, `type == "string_too_short"` and `code == "STRING_TOO_SHORT"`.
- **Valid body:** a valid body still returns 200.

**This will break existing tests.** About 58 test files compare the whole 422 response to an exact dict. Because those dicts don't include `code`, the extra field will make those tests fail, even though API clients are unaffected. You'd need to either update those expected bodies, or make `code` opt-in (for example, a custom handler or an app setting) instead of the default.

To go ahead, approve the file writes and tell me whether to update the existing tests too. I'll then apply the change and run `pytest -W ignore tests/test_validation_error_code.py`.