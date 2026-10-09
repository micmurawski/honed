Let me look at the routing module to find where startup handlers are passed through to Starlette.

Now I have a complete picture. Let me look at the APIRouter `__init__` signature to add the parameter in the right place.

Now I have a complete picture. Let me implement the changes.

**Step 1**: Modify `fastapi/routing.py` to add `on_startup_error` to `APIRouter.__init__` and wrap `_startup()` handlers with try/except.

**Step 2**: Modify `fastapi/applications.py` to add `on_startup_error` to `FastAPI.__init__()` and pass it through to the APIRouter.

**Step 3**: Create the test file.

**Step 4**: Run the tests.

`FastAPI` doesn't expose `add_event_handler` directly. I need to use the `on_startup` constructor parameter (which the task description references).

Both tests pass. Let me also verify the existing tests still work to make sure I didn't break anything.

All existing related tests pass. The `inline-snapshot` warning is a plugin formatter complaint unrelated to my changes. Let me do a final smoke test.

The parameter is correctly defaulted to `None` and properly threaded through to the router. Let me run the full requested command one more time to confirm:

Both tests pass.

## Summary

**Files changed:**

1. **`fastapi/routing.py`**
   - Added `on_startup_error: Callable[[Exception], None] | None = None` parameter to `APIRouter.__init__()` (with a `Doc` description), placed right after `on_startup`.
   - Stored it as `self.on_startup_error = on_startup_error`.
   - Modified `APIRouter._startup()` to wrap each handler call in `try/except`. If `self.on_startup_error` is set, the exception is passed to the callback; otherwise it is re-raised, preserving existing behavior.

2. **`fastapi/applications.py`**
   - Added the matching `on_startup_error` parameter to `FastAPI.__init__()` (with a `Doc` description), placed right after `on_startup`.
   - Threaded the value through to the underlying `routing.APIRouter` constructor.

3. **`tests/test_startup_error_handler.py`** (new)
   - `test_startup_error_propagates_without_callback` — registers a failing startup handler with `on_startup_error=None` and asserts the `RuntimeError("db unavailable")` propagates out of `TestClient.__enter__`.
   - `test_startup_error_captured_with_callback` — registers a failing startup handler with a custom `on_startup_error` callback that appends to a list. Asserts the exception was captured, startup completed, and a `GET /ping` request returns `200 {"pong": "ok"}`.

**Test results:** `pytest -W ignore tests/test_startup_error_handler.py` → 2 passed. Existing related tests (`test_router_events.py`, `test_application.py`) still pass.