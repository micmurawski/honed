Now I have a clear picture. Let me check what tests already exist to understand the test setup pattern:

Tests run with the venv. Let me create a helper script to simplify subsequent runs:

Now I have enough context. Let me start implementing. Let me set up the plan before making changes:

Let me look at the exact field placements before I edit:

Now let me start implementing. First, I'll add the `default_response_headers` parameter to `_populate_api_route_state`:

Now let me update `APIRoute.__init__`:

Now add `default_response_headers` to the APIRoute class attributes hint and update `get_route_handler`:

Now update `APIRoute.get_route_handler` to pass the new param through:

Now update `get_request_handler` to accept and apply default headers:

Now apply the default headers at the end, right before the response is returned:

Now update `_EffectiveRouteContext` to also have `default_response_headers`:

Now update `_EffectiveRouteContext.from_api_route` to populate `default_response_headers` from the original route:

Now update `APIRouter.add_api_route`:

Now I need to check if APIRouter already has a `default_response_headers` attribute. Let me check:

Let me revert the router-level inheritance part — the task only requires `APIRoute.__init__` and decorator propagation:

Now I need to add the param to the call to `route_class(...)` in `add_api_route`:

Now update `APIRouter.api_route`:

Now I need to update all the verb helpers. Let me first look at the existing Doc-wrapped style:

Let me check the documentation string wording in both files:

Let me look more carefully at the precise pattern used in the verb helpers:

Now I'll do a `replaceAll` for the 8 verb helpers in `fastapi/routing.py`:

Now I need to also propagate the parameter in each helper's call to `self.api_route(...)`. Let me find and update those:

Now let me update `fastapi/applications.py`. First the `FastAPI.add_api_route`:

Now `FastAPI.api_route`:

Now let me verify both files parse correctly:

The propagation works. Now let me also test the override case:

The handler's value wins for `Cache-Control`, but `X-Source` (not overridden) is still injected. Let me also test with `APIRouter`:

Now let me write the test file:

Now let me run the tests:

All 4 tests pass. Let me also run the requested command exactly as specified by the user (without `-v`):

Let me also verify all HTTP verb decorators work:

All decorators propagate correctly. Let me now run the broader test suite to make sure I haven't broken anything:

Let me also run a few broader related tests:

Let me run a larger selection of tests to verify nothing else is broken:

Let me run the exact command specified in the task:

All 4 tests pass. Let me also do a final review of the changes I made by reading the key sections:

Everything looks good. Let me also do a final test using the Task's exact test requirements from step 5:

All task requirements are verified. Let me give a summary of the changes:

## Summary

I added support for per-route default response headers to FastAPI. The changes:

### `fastapi/routing.py`

1. **`_populate_api_route_state`** — Added `default_response_headers: dict[str, str] | None = None` parameter. Stores a defensive copy on `route.default_response_headers` (or `{}` if `None`).

2. **`APIRoute.__init__`** — Added the same `default_response_headers: dict[str, str] | None = None` parameter; forwards to `_populate_api_route_state`.

3. **`APIRoute.get_route_handler`** — Forwards `route.default_response_headers` to `get_request_handler`.

4. **`get_request_handler`** — Added `default_response_headers: Mapping[str, str] | None = None` parameter. Just before returning the response, applies each default header *only* if the response does not already have a value for that key (case-insensitive). This guarantees handler-set headers always win.

5. **`_EffectiveRouteContext`** — Added `default_response_headers: dict[str, str] = field(default_factory=dict)` and populated it inside `from_api_route` so the feature works correctly when routes are nested via `include_router`.

6. **`APIRouter.add_api_route`, `APIRouter.api_route`** — Plumb the parameter through to the underlying `APIRoute`.

7. **`APIRouter.{get,put,post,delete,options,head,patch,trace}`** — All 8 verb helpers in `routing.py` now accept `default_response_headers: dict[str, str] | None = None` (with the same `Annotated[..., Doc(...)]` style the codebase uses for documentation) and pass it through to `self.api_route(...)`.

### `fastapi/applications.py`

1. **`FastAPI.add_api_route`, `FastAPI.api_route`** — Plumb the parameter through to `self.router.add_api_route`.

2. **`FastAPI.{get,put,post,delete,options,head,patch,trace}`** — All 8 verb helpers in `applications.py` accept `default_response_headers` and pass it through to `self.router.<verb>(...)`.

### `tests/test_route_default_headers.py` (new)

Four tests covering the requirements:
- `test_default_response_headers_are_injected` — registers `@app.get("/cached-item", default_response_headers={"Cache-Control": "max-age=300", "X-Source": "db"})` and verifies both headers appear.
- `test_handler_set_header_is_not_overridden` — verifies the handler's `Cache-Control: no-store` wins over the route's `max-age=300` default, while `X-Source` (not overridden) is still injected.
- `test_default_response_headers_propagate_via_router` — verifies the parameter flows through `APIRouter` decorators + `include_router`.
- `test_missing_default_response_headers_is_a_noop` — verifies backwards compatibility when the parameter is omitted.

### Test result

```
$ pytest -W ignore tests/test_route_default_headers.py
4 passed in 0.29s
```