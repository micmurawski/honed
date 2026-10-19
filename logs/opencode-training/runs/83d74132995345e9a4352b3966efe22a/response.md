Now I have enough understanding. Let me start making the changes.

Now I'll start making changes. First, let me update `_APIRouteLike` Protocol and `_populate_api_route_state`:

Now update `_populate_api_route_state`:

Now update `APIRoute.__init__()`:

Now update `_EffectiveRouteContext`:

Now update `from_api_route` to propagate the callable:

Now update `APIRouter.add_api_route()`:

Now update `APIRouter.api_route()`:

Now I need to update all the HTTP verb methods on `APIRouter`. Let me look at them and update them one by one. The `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace` all have similar structure. Let me look at `get` first:

Let me use a Python script for the more complex multi-edit operations:

Now do the same for applications.py:

Now do the same for applications.py:

Now I also need to add the parameter to `FastAPI.add_api_route()` and `FastAPI.api_route()`. Let me check:

Now let me update `fastapi/openapi/utils.py` to evaluate the callable:

Now let me update the two check sites in openapi/utils.py:

Now let me add the helper function. Let me find a good place to add it:

Now let me run the existing tests to make sure I haven't broken anything:

All 248 relevant tests pass. Now let me write the test file:

All 3 tests pass. Let me also do a broader sanity check that I haven't broken anything:

Let me also run a final sanity check using graphify to make sure the knowledge graph remains valid:

All 3 tests pass. Let me also run the graphify update to keep the knowledge graph current:

All changes are complete and tests pass. Here's a summary of what was done:

## Changes

### `fastapi/routing.py`
- Added `include_in_schema_if: Callable[[], bool] | None = None` to:
  - `_APIRouteLike` Protocol
  - `_populate_api_route_state()` helper (and stored on route)
  - `APIRoute.__init__()`
  - `_EffectiveRouteContext` dataclass (propagated in `from_api_route()`)
  - `APIRouter.add_api_route()` and `APIRouter.api_route()` decorator
  - All 8 HTTP verb methods on `APIRouter` (get, put, post, delete, options, head, patch, trace)

### `fastapi/applications.py`
- Added `include_in_schema_if: Callable[[], bool] | None = None` to:
  - `FastAPI.add_api_route()` and `FastAPI.api_route()` decorator
  - All 8 HTTP verb methods on `FastAPI` (get, put, post, delete, options, head, patch, trace)
- Threaded the value into `self.router.add_api_route(...)` calls

### `fastapi/openapi/utils.py`
- Added `_route_should_be_included_in_schema()` helper that evaluates the callable when set, falling back to the `include_in_schema` boolean otherwise
- Updated `get_openapi_path()` and `get_fields_from_routes()` to use the new helper

### `tests/test_include_in_schema_if.py`
3 tests:
- `test_feature_flag_excluded_when_callable_returns_false` — `/public` appears, `/feature-flag` does not
- `test_feature_flag_included_when_callable_returns_true` — flips the flag and clears the cached schema, both paths now appear
- `test_routes_still_responds_regardless_of_schema_inclusion` — runtime responses are unaffected by the schema toggle

## Test result

```
tests/test_include_in_schema_if.py::test_feature_flag_excluded_when_callable_returns_false PASSED
tests/test_include_in_schema_if.py::test_feature_flag_included_when_callable_returns_true PASSED
tests/test_include_in_schema_if.py::test_routes_still_responds_regardless_of_schema_inclusion PASSED
============================== 3 passed in 0.22s ===============================
```

All 248 existing include/openapi tests still pass; only pre-existing test failures (missing `pydantic_settings`, missing `fastapi[standard]`) remain, unrelated to these changes.