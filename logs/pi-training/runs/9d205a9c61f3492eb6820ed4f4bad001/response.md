Now I have enough context. Let me implement the changes step by step.

**Step 1: Update `fastapi/routing.py`** - Modify `_APIRouteLike` Protocol, `_populate_api_route_state`, `APIRoute.__init__`, and the helper methods.

Now update `APIRoute.__init__` to accept the new parameter:

Now let me also update the `_APIRouteLike` Protocol instance further down (there's a second one):

Now let me update `APIRouter.add_api_route` to accept and forward `include_in_schema_if`:

Now let me update `APIRouter.api_route` (the decorator factory):

Now I need to update all7 decorator methods in `routing.py` (`get`, `post`, `put`, `delete`, `options`, `head`, `patch`) to accept and forward `include_in_schema_if`. They all follow the same pattern. Let me look at one to find the exact `include_in_schema` block:

Now let me add the `include_in_schema_if` parameter to all 7+1 decorator methods in `routing.py` with a global replace:

Let me use a Python script to make the remaining edits efficiently:

**Step 3: Update `fastapi/openapi/utils.py`** to evaluate `include_in_schema_if` at schema generation time:

**Step 4: Update `fastapi/applications.py`** - Propagate through decorator helpers. Let me find the relevant locations:

The implementation works. Now let me write the test file:

All 3 tests pass. Let me also run the full FastAPI test suite to make sure I didn't break anything:

Now let me also update the graph after making these code changes, as per project guidelines:

All 3 tests pass. Here's a summary of the changes:

## Summary

**`fastapi/routing.py`** — Added `include_in_schema_if: Callable[[], bool] | None = None` parameter to:
- `APIRoute.__init__` and the shared `_populate_api_route_state` helper
- Both `_APIRouteLike` Protocol definitions
- `APIRouter.add_api_route` (passes through to the route constructor)
- `APIRouter.api_route` decorator factory
- All 8 HTTP-method decorator methods on `APIRouter` (`get`, `post`, `put`, `delete`, `options`, `head`, `patch`, `trace`)

**`fastapi/openapi/utils.py`** — Added `_route_should_be_in_schema(route)` helper that:
- Returns `False` if `route.include_in_schema` is `False`
- Returns `False` if `route.include_in_schema_if` is provided and returns `False`
- Otherwise returns `True`
- Replaced both `if route.include_in_schema:` checks (in `get_fields_from_routes` and `get_openapi_path`) with `_route_should_be_in_schema(...)`. This evaluates the callable at schema generation time on every `/openapi.json` request.

**`fastapi/applications.py`** — Propagated the new parameter through:
- `FastAPI.add_api_route`
- `FastAPI.api_route` decorator factory
- All 8 HTTP-method decorators (`get`, `post`, `put`, `delete`, `options`, `head`, `patch`, `trace`)

**`tests/test_include_in_schema_if.py`** — Three tests:
1. `/public` (no condition) appears in `/openapi.json`
2. `/feature-flag` (with `include_in_schema_if=lambda: False`) is excluded
3. After monkey-patching the predicate to `lambda: True`, `/feature-flag` now appears

Test result: `3 passed in 0.29s`