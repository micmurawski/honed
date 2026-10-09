Now I have enough context. Let me implement the changes. First, I'll update `fastapi/routing.py`:

Now update the `APIRoute` class type hints and `_populate_api_route_state`:

Now update `_populate_api_route_state` to accept and store the new field:

Now update `APIRoute.__init__` to accept and forward the new parameter:

Now the APIRouter.add_api_route → APIRoute() call:

Now the APIRouter.api_route decorator → add_api_route call:

Now let me update `fastapi/applications.py`:

Now let me update `fastapi/openapi/utils.py` to evaluate the callable at schema generation time:

Now let me write the tests:

Now let me run the tests:

All 5 tests pass. Let me also make sure I didn't break any existing tests:

Let me run the new test file once more with the exact command requested:

All 5 tests pass. Here's a summary of what I did:

## Changes

### `fastapi/routing.py`
- Added `include_in_schema_if: Callable[[], bool] | None` attribute to the `_APIRouteLike` Protocol and to the `APIRoute` class.
- Added the `include_in_schema_if: Callable[[], bool] | None = None` parameter (with `Annotated[..., Doc(...)]` on the public decorators) and forwarded it through:
  - `_populate_api_route_state()` — stores it as `route.include_in_schema_if`.
  - `APIRoute.__init__()` → `_populate_api_route_state`.
  - `APIRouter.add_api_route()` → `route_class(...)`.
  - `APIRouter.api_route()` decorator → `add_api_route()`.
  - All 8 HTTP method decorators in `APIRouter` (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`) → `self.api_route()`.
- Propagated `include_in_schema_if` through `_EffectiveRouteContext.from_route()` so the callable survives inclusion of a router.

### `fastapi/applications.py`
- Added `include_in_schema_if` to `FastAPI.api_route()` and forwarded it to `self.router.add_api_route()`.
- Added `include_in_schema_if: Annotated[Callable[[], bool] | None, Doc(...)] = None` to all 8 FastAPI HTTP method decorators (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`) and forwarded it to the router's matching decorator.

### `fastapi/openapi/utils.py`
- Added a helper `_resolve_include_in_schema(route)`:
  - If the route has `include_in_schema_if`, returns `bool(include_in_schema_if())`.
  - Otherwise returns `route.include_in_schema`.
- Replaced the two boolean checks (`if route.include_in_schema:` in `get_openapi_path()` and `if api_route.include_in_schema:` in `get_fields_from_routes()`) with calls to the helper.

### `tests/test_include_in_schema_if.py` (new)
Five tests, all passing:
1. `test_include_in_schema_if_excludes_when_callable_returns_false` — the exact scenario from the task: `GET /public` (no condition) and `GET /feature-flag` with `include_in_schema_if=lambda: False`; `/openapi.json` shows `/public` but not `/feature-flag`.
2. `test_include_in_schema_if_includes_when_callable_returns_true` — same with the callable returning `True`.
3. `test_include_in_schema_if_can_be_toggled_at_runtime` — toggles the flag, clears `app.openapi_schema`, re-fetches, asserts the route appears.
4. `test_include_in_schema_if_patched_to_return_true` — uses `unittest.mock.patch.dict` to swap the dict the closure reads, re-fetches, asserts `/feature-flag` appears.
5. `test_default_behavior_when_include_in_schema_if_is_none` — confirms that omitting `include_in_schema_if` preserves the existing `include_in_schema` boolean semantics.

## Test run

```
$ pytest -W ignore tests/test_include_in_schema_if.py -v
...
tests/test_include_in_schema_if.py::test_include_in_schema_if_excludes_when_callable_returns_false PASSED [ 20%]
tests/test_include_in_schema_if.py::test_include_in_schema_if_includes_when_callable_returns_true PASSED [ 40%]
tests/test_include_in_schema_if.py::test_include_in_schema_if_can_be_toggled_at_runtime PASSED [ 60%]
tests/test_include_in_schema_if.py::test_include_in_schema_if_patched_to_return_true PASSED [ 80%]
tests/test_include_in_schema_if.py::test_default_behavior_when_include_in_schema_if_is_none PASSED [100%]
============================== 5 passed in 0.32s ===============================
```

Sanity-checked the surrounding suite (`test_include_route`, `test_include_router_defaults_overrides`, `test_router_include_context`, `test_param_include_in_schema`, `test_application`, `test_openapi_route_extensions`, `test_openapi_separate_input_output_schemas`, `test_custom_schema_fields`, `test_custom_route_class`, `test_extra_routes`) — 124 tests, all green.