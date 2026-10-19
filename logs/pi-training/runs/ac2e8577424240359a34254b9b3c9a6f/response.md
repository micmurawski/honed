Now I have a clear understanding. Let me implement the feature.

## Step 1: Modify `fastapi/routing.py`

### 1a. Add parameter to `_populate_api_route_state`:

### 1c. Add parameter to `APIRoute.__init__`:

### 1e. Add type annotation in `APIRoute` class declaration:

Now the second occurrence in `APIRoute`:

### 1f. Modify `APIRoute.get_route_handler()` to wrap and inject default headers:

### 1h. Add field to `_EffectiveRouteContext` and `from_api_route`:

Now update `from_api_route` to copy the default headers from the original route:

### 1i. Update `APIRouter.add_api_route` to accept and pass the parameter:

### 1j. Update `APIRouter.api_route`:

### 1k. Update `APIRouter.get/post/put/delete/patch/head/options/trace`:

Let me look at the patterns for these methods. They all call `self.api_route(...)` at the end. They have long parameter lists — I need to add `default_response_headers` to each.

Now the call to `self.api_route(...)`:

Now let's look at `put`, `post`, `delete`, `patch`, `head`, `options`, `trace`. Each follows similar pattern but their signature is followed by `methods=["PUT"]` etc. Let me look at each to find the unique pattern.

Let me check the current state of routing.py and use a script for the bulk changes:

Now DELETE:

Now OPTIONS:

Now HEAD:

Now TRACE:

Let me verify the state:

All routing.py changes done. Now let me verify the module is syntactically valid:

Good, FastAPI imports cleanly. Now let me apply the same to `applications.py`:

## Step 2: Modify `fastapi/applications.py`

Now `api_route`:

Now the HTTP method decorators on `FastAPI`. They all follow a similar pattern. Let me check what the FastAPI-level `add_api_route` looks like and the first method (get):

Now PUT through TRACE. Same pattern as before — let me do PUT, then the rest:

Now DELETE:

Now OPTIONS, HEAD, PATCH, TRACE — let me batch these in similar edits:

Now HEAD:

Now PATCH:

Now TRACE:

Let me verify the state of applications.py:

Now let me verify everything imports correctly:

Now let me write the tests:

## Step 3: Write tests

Both tests pass. Let me also run the broader test suite to make sure I haven't broken anything:

Existing tests still pass. Let me run a broader set:

Run the test once more as requested by the user:

Both tests pass.

## Summary

I added `default_response_headers: dict[str, str] | None = None` to FastAPI's per-route configuration.

**Changes to `fastapi/routing.py`:**
- Added the parameter to `_populate_api_route_state()` and assigned `route.default_response_headers` there.
- Added the parameter to `APIRoute.__init__()`, propagated through `_populate_api_route_state`.
- Declared the new attribute on both `_APIRouteLike` (Protocol) and `APIRoute` classes.
- Modified `APIRoute.get_route_handler()` to wrap the inner app: if `default_response_headers` is set, the wrapper applies each header to the response only when the route handler didn't already set it (case-insensitive check via `response.headers`).
- Added the field to `_EffectiveRouteContext` dataclass and `from_api_route()` so headers survive `include_router()`.
- Added the parameter to `APIRouter.add_api_route()`, `APIRouter.api_route()`, and the HTTP-method decorators `get/put/post/delete/patch/head/options/trace()`.

**Changes to `fastapi/applications.py`:**
- Propagated the parameter through `FastAPI.add_api_route()`, `FastAPI.api_route()`, and the HTTP-method decorators on `FastAPI` (`get/put/post/delete/patch/head/options/trace`).

**New test file `tests/test_route_default_headers.py`:**
- `test_default_response_headers_are_applied`: registers `GET /cached-item` with `default_response_headers={"Cache-Control": "max-age=300", "X-Source": "db"}` and verifies both appear in the response.
- `test_handler_explicit_headers_win_over_defaults`: registers `GET /handler-override` with the same defaults, but the handler sets `Cache-Control: no-store` via the `response: Response` parameter — verifies `no-store` wins while `X-Source` still gets the default.

Test result: `2 passed in 0.19s`. The full route/router-related test suite (`test_route_default_headers.py`, `test_default_response_class_router.py`, `test_default_response_class.py`, `test_custom_route_class.py`, `test_include_router_defaults_overrides.py`) — 75 passed, 3 skipped, 0 failed.