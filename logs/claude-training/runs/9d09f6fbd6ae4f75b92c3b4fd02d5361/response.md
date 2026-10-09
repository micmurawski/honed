Both constructors are keyword-only, so I can put the new param next to `on_shutdown`. Now making the edits.

I couldn't make any changes: every edit was blocked because write permission for `fastapi/` hasn't been granted. No files were modified, no test file was written, and pytest wasn't run. The `graphify query` command also needed approval, so I read the source directly with targeted reads instead.

**What I found**
- `FastAPI.__init__()` passes `on_startup` to its own `routing.APIRouter` (`fastapi/applications.py:984-990`), not to Starlette.
- Starlette's Router no longer runs startup handlers (it dropped them in Kludex/starlette#3117). FastAPI keeps a copy of the old `_DefaultLifespan` (`fastapi/routing.py:250`). Its `__aenter__` calls `APIRouter._startup()` (`fastapi/routing.py:6364`), and that is where each handler runs. So the try/except goes there, not in Starlette.
- `include_router()` copies a sub-router's startup handlers into the parent (`fastapi/routing.py:3313`). The app-level callback would therefore also catch errors from included routers' handlers.
- Both constructors only accept keyword arguments, so adding the new parameter next to `on_shutdown` won't break existing callers.

**Planned changes**
1. `fastapi/applications.py`: add `on_startup_error: Callable[[Exception], None] | None = None` after `on_shutdown`, with a `Doc(...)` description, and pass it to `routing.APIRouter(...)`.
2. `fastapi/routing.py`, in `APIRouter.__init__`: add the same parameter and store it as `self.on_startup_error`.
3. `fastapi/routing.py`, in `_startup()`:
   ```python
   for handler in self.on_startup:
       try:
           if is_async_callable(handler):
               await handler()
           else:
               handler()
       except Exception as exc:
           if self.on_startup_error is None:
               raise
           self.on_startup_error(exc)
   ```
   With no callback, the error is re-raised with a bare `raise`, so existing code behaves the same. With a callback, the error goes to the callback and the remaining startup handlers still run.
4. `tests/test_startup_error_handler.py`: a startup handler raises `RuntimeError("db unavailable")`.
   - With no callback, `with TestClient(app):` raises that error (checked with `pytest.raises`).
   - With a callback that appends to a list, startup completes, a later handler still runs, and a request succeeds.
5. Run `pytest -W ignore tests/test_startup_error_handler.py`, then `graphify update .`.

If you allow edits to `fastapi/` and `tests/` (or approve the prompts) and re-run, I'll apply these changes and run the tests.