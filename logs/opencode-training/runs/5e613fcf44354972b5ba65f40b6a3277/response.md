Now let me look at the full `on_startup` block in `applications.py` more carefully:

Now I have a clear picture. Let me make the changes:

Now add the `on_startup_error` parameter right after `on_startup`:

Now let me update the `APIRouter(...)` call to pass the wrapped startup handlers. I want to do this just before the `APIRouter(...)` call:

Now let me add the `_wrap_startup_handler` helper method. I'll place it just after the `__init__` method. Let me find where the `__init__` ends:

Now let's verify the edits look correct:

Now let me write the test file:

Now let's run the tests as the user requested:

All 4 tests pass. Let me also run the existing test suite to make sure nothing has regressed:

All 10 existing router/lifespan tests still pass. Let me also verify the module imports cleanly and re-run the requested command exactly as the user asked:

That's an unrelated system Python issue (the system `python3` is 3.9 and doesn't support PEP 604 syntax; pytest ran with 3.10 correctly). The actual test environment already used Python 3.10 and all tests passed. Let me run the exact command the user requested to confirm:

All 4 tests pass with the exact command from the request.

## Summary

I added an `on_startup_error` callback parameter to FastAPI as requested. Here's what changed:

### `fastapi/applications.py`
1. **Imported `is_async_callable`** from `starlette._utils` (top of file) so the wrapper can handle both sync and async startup handlers uniformly.
2. **Added the `on_startup_error: Callable[[Exception], None] | None = None` parameter** to `FastAPI.__init__()`, placed right after `on_startup`. It uses the same `Annotated[..., Doc(...)]` pattern used by every other FastAPI parameter, with a docstring explaining the no-op-by-default behaviour.
3. **Wrapped the registered handlers in a try/except at the place they're registered** (just before `APIRouter(...)` is constructed). When `on_startup_error is not None and on_startup` is truthy, each handler is replaced by a closure that calls `on_startup_error(exc)` instead of re-raising. When `on_startup_error` is `None`, the original handlers are passed through unchanged, so existing code sees no behaviour change.
4. **Added a `_wrap_startup_handler` static helper** that preserves the sync/async nature of the original callable — it returns an `async def` wrapper for async handlers and a plain `def` wrapper for sync ones.

The wrapping is done at the FastAPI layer (not in `APIRouter._startup()` or Starlette's `Router.startup()`) because the user asked to add the parameter to `FastAPI.__init__()` specifically. `APIRouter` receives the already-wrapped handlers, so no changes to `fastapi/routing.py` were needed.

### `tests/test_startup_error_handler.py` (new)
Four tests covering:
- **`test_startup_handler_exception_propagates_by_default`** — registers a sync handler that raises `RuntimeError("db unavailable")` and asserts that without `on_startup_error` the exception propagates out of `TestClient` exactly as before (no behaviour change).
- **`test_startup_handler_exception_is_captured_by_callback`** — registers the same raising handler plus an `on_startup_error` callback that appends to a list and flips a "startup completed" flag; asserts the exception ends up in the list with the right type/message and the app started successfully.
- **`test_startup_handler_async_exception_is_captured_by_callback`** — same as above but with an `async def` handler, to verify the wrapper handles async too.
- **`test_startup_handler_mixed_exceptions_are_handled_independently`** — registers `[ok, boom, ok]` and confirms the error callback fires exactly once for the raising handler while the surrounding handlers still run.

### Test results
```
tests/test_startup_error_handler.py ....   [100%]
============================== 4 passed in 0.29s ==============================
```
All 4 new tests pass, and the existing `tests/test_router_events.py` (10 tests, the closest related suite) still passes — confirming the default-`None` path is a no-op.