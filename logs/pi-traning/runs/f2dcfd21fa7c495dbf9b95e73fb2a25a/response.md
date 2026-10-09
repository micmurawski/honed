Now let me add `strict_slashes: bool = True` to `_RouterIncludeContext`:

Now update `for_include` to propagate `strict_slashes` from the included router:

Now update `combine` to propagate `strict_slashes`:

Now let me look at `from_api_route` to add `strict_slashes`:

Now let's update `_populate_api_route_state` to accept `strict_slashes`:

Now let's add `strict_slashes` to `APIRoute.__init__`:

Now let me store `strict_slashes` on the router:

Now let's add `strict_slashes` to `add_api_route` to propagate to APIRoute:

Now let me create the test file:

Now let's run the tests:

All 5 tests pass. Let me also update the graph to keep it current, as required by the project instructions:

All 5 tests pass. Here's a summary of the changes:

## Summary

I added router-level `strict_slashes` support to FastAPI. When `strict_slashes=False`, routes defined on that router match both `/items` and `/items/` without redirecting.

### `fastapi/routing.py` changes

1. **`APIRouter.__init__()`** — Added `strict_slashes: bool = True` parameter. Stored as `self.strict_slashes` on the router.

2. **`APIRoute.__init__()`** — Added `strict_slashes: bool = True` parameter (also declared as a class attribute for type info).

3. **`_populate_api_route_state()`** — Added `strict_slashes` parameter. Stores it on the route, and — when `strict_slashes=False` and the path is not `"/"` — recompiles the path regex by rewriting the trailing `$` to `/?$` so the route matches both with and without a trailing slash.

4. **`APIRouter.add_api_route()`** — Added `strict_slashes` parameter (optional override) and propagates the router's value as the default when not provided, then passes it through to `APIRoute`.

5. **`_RouterIncludeContext`** — Added `strict_slashes: bool = True` field; populated from the included router in `for_include()` and AND-combined in `combine()` so nested includes keep the looser setting.

6. **`_EffectiveRouteContext.from_api_route()`** — Passes `include_context.strict_slashes` down to `_populate_api_route_state` so the rebuilt path regex (after the include prefix is applied) still has the optional-trailing-slash behavior.

### `fastapi/applications.py`

`FastAPI.include_router()` already delegates to `APIRouter.include_router`, which already builds the `_RouterIncludeContext` with the included router's `strict_slashes`. The values are carried through end-to-end without a new parameter.

### `tests/test_strict_slashes.py` (new)

Five tests covering:
- `strict_slashes=False`: both `/users` and `/users/` return 200 with the same JSON.
- `strict_slashes=True` (explicit) and default: `/users2` returns 200, `/users2/` returns 307 or 404.
- `strict_slashes=False` propagates to the route's `path_regex` and `strict_slashes` attribute.
- `strict_slashes=True` (default) keeps the strict regex that only matches the canonical form.

`pytest -W ignore tests/test_strict_slashes.py` → 5 passed. All other existing tests (including `test_router_redirect_slashes.py`) continue to pass.