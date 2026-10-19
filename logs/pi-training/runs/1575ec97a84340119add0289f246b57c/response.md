Now I have a clear picture. Let me look at the APIRouter signature and find tests for `pytest.raises` patterns.

Now I have all the context I need. Let me implement the changes. First, add the `on_startup_error` parameter to `FastAPI.__init__()`:

Now let me add the `on_startup_error` parameter to `APIRouter.__init__()` in `routing.py` and update the `_startup()` method:

Let me verify the changes look correct:

Now let me write the test file:

Now let me run the tests:

All 5 tests pass. Let me also verify the existing router events tests still pass to confirm no regression:

All existing tests pass. Let me give a summary of what I did:

## Summary

### 1. `fastapi/applications.py` — `FastAPI.__init__()`
- Added `on_startup_error: Callable[[Exception], None] | None = None` parameter, placed right after `on_startup` for discoverability, with full `Annotated` + `Doc()` description.
- Passed it through to `routing.APIRouter(...)` so the router's `_startup()` can use it.

### 2. `fastapi/routing.py` — `APIRouter`
- Added the same `on_startup_error` parameter to `APIRouter.__init__()`.
- Stored it as `self.on_startup_error: Callable[[Exception], None] | None`.
- Wrapped each handler invocation in `APIRouter._startup()` in `try/except Exception`:
  - If `self.on_startup_error` is set, the callback is invoked with the exception.
  - Otherwise, the exception is re-raised via `raise`, preserving existing behaviour.

### 3. `tests/test_startup_error_handler.py` — 5 tests
- `test_on_startup_error_propagates_when_not_set` — default (`on_startup_error=None`) lets the `RuntimeError("db unavailable")` propagate out of the `TestClient` context.
- `test_on_startup_error_captures_exception` — with a callback, the exception is captured in a list, startup completes, and the app still serves requests.
- `test_on_startup_error_called_per_failing_handler` — callback is called once per failing handler (not for successful ones).
- `test_on_startup_error_default_is_none` — introspection check that the default is `None`.
- `test_on_startup_error_with_async_handler` — same behaviour works for `async def` startup handlers.

### 4. Test results
```
tests/test_startup_error_handler.py ..... 5 passed in 0.24s
tests/test_router_events.py ......... 10 passed in 0.33s
```

No regressions in the existing `test_router_events.py` suite (which exercises the same `_startup()` code path).