Now I understand the structure. Let me check the existing test patterns once more to ensure I align with the codebase, then implement.

Now I'll modify the default `request_validation_exception_handler` to add a `code` field per error:

Now let me create the test file:

Now let me run the tests:

The error type from Pydantic 2.13 for a string body is `string_type` (not `string_too_short`). Let me investigate what Pydantic actually returns and adjust the test:

I see — without `embed=True`, FastAPI expects the body itself to be a string. The `string_type` error is because I sent an object instead. Let me fix the test to send a proper string body and verify:

The change works as expected. Let me update the test to send a short string body (matching what `Body(min_length=5)` expects):

Now let me run the tests as requested by the user:

Both tests pass. Let me also confirm the changes don't break any other existing tests that exercise the default 422 handler, by spot-checking a few representative ones:

As expected, existing tests with exact-match assertions on 422 bodies now fail because they don't account for the new `code` field. The API itself is backwards-compatible (existing fields preserved), but the test fixtures need the new field. Let me find all affected tests:

The implementation is correct and the requested new test passes. Let me verify the final state of both files:

Per the project's AGENTS.md, I should keep the graphify knowledge graph current after modifying code. Let me do that:

The `graphify` CLI isn't installed (only the plugin reminder exists), so that step isn't actionable here. The task as specified is complete. Let me run the exact command the user requested:

All 2 tests pass. Summary of changes:

## Changes

### `fastapi/exception_handlers.py`
Modified `request_validation_exception_handler` to add a `code` field to each error item. The `code` is derived from the Pydantic error `type`, normalized to uppercase with dots replaced by underscores. The original `loc`, `msg`, and `type` fields are preserved.

```python
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details: list[dict] = []
    for error in exc.errors():
        detail = jsonable_encoder(error)
        error_type = detail.get("type", "")
        detail["code"] = str(error_type).upper().replace(".", "_")
        details.append(detail)
    return JSONResponse(
        status_code=422,
        content={"detail": details},
    )
```

A `str(...)` coercion on `error_type` defends against any future non-string type values. The transformation is additive (a new dict field) so existing API consumers keep working.

### `tests/test_validation_error_code.py` (new)
Two tests:

1. **`test_validation_error_includes_code`** — POSTs the short string body `"hi"` to an endpoint with `name: str = Body(min_length=5)`, asserts 422, then for each error checks that `code` is present, is a string, matches `^[A-Z][A-Z0-9_]*$`, equals `"STRING_TOO_SHORT"`, and that `loc`/`msg`/`type` are still intact.
2. **`test_validation_error_code_for_missing_body`** — POSTs `null` body to the same endpoint and asserts every error's `code` matches the pattern (covers a different code path: missing body).

## Sample output

```json
{"detail": [{
  "type": "string_too_short",
  "loc": ["body"],
  "msg": "String should have at least 5 characters",
  "input": "hi",
  "ctx": {"min_length": 5},
  "code": "STRING_TOO_SHORT"
}]}
```

## Test run

```
$ pytest -W ignore tests/test_validation_error_code.py
collected 2 items
tests/test_validation_error_code.py ..                                   [100%]
============================== 2 passed in 0.18s ===============================
```

## Heads-up on broader test suite

**In short:** the change is fully backwards-compatible at the API contract level (existing fields preserved, only `code` added), but several existing tests across the suite use strict equality (`assert response.json() == {...}`) on 422 bodies and will fail because they don't include `"code": "..."`. Examples I noticed: `tests/test_path.py::test_path_int_foobar`, `tests/test_annotated.py`, `tests/test_security_oauth2.py`, the `tests/test_request_params/**`, `tests/test_tutorial/**` tree, and many more.

You didn't ask me to update those, so I left them alone. If you'd like, I can follow up by updating the affected fixtures to add the matching `code` for each error — that's a mechanical sweep (about ~150+ occurrences across many files). Let me know if you want me to do that.