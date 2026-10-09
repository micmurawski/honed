Now let me look at the routing.py to understand how `APIRoute` consumes `response_model_exclude_none`.

Let me look at `APIRouter.get` and `add_api_route`:

Let me look at the `APIRouter.get` definition:

Now I have a clear picture. Let me look at all HTTP method decorators in `FastAPI`:

Let me look at how `strict_content_type` is implemented as a reference example for a similar `bool` parameter:

Now let me check how `APIRouter.__init__` receives `default_response_class`:

Now I have a clear picture. Let me start implementing.

## Step 1: Modify `fastapi/applications.py`

First, add the `default_response_model_exclude_none` parameter to `FastAPI.__init__()`:

Now store it on `self` and pass it to `APIRouter`:

Now I need to update the `FastAPI.add_api_route`, `FastAPI.api_route`, and all HTTP method decorators. Let me look at how they look now:

Let me update all8 decorator methods (get, put, post, delete, options, head, trace, patch) in `applications.py`:

Let me use Python to update all8 occurrences in a batch:

Let me verify the imports:

Now let me view the modified `__init__` to verify:

Good. Now let me verify the `self.default_response_model_exclude_none` was set:

## Step 2: Modify `fastapi/routing.py`

Now add `default_response_model_exclude_none` to `APIRouter.__init__()`:

Now store `default_response_model_exclude_none` on the router instance:

Now update `APIRouter.add_api_route` to use `bool | DefaultPlaceholder` and resolve via `get_value_or_default`:

Let me view the two occurrences:

Now I need to resolve the default in `add_api_route`. Let me find the call to `route_class(...)`:

Now update all8 HTTP method decorator methods in `routing.py`:

Let me verify the changes in routing.py:

Let me verify the syntax of both files:

Let me verify FastAPI imports work and basic functionality still functions:

## Step 3: Create the test file

Now let's run the tests:

Let me check how `get_request_handler` is called. The issue is that the value still arrives as a `DefaultPlaceholder`:

I need to fix the logic. The issue is that `get_value_or_default` returns the first `DefaultPlaceholder` if all are `DefaultPlaceholder`s. Let me change the approach:

Now change the `APIRouter.__init__` default value to be a plain `bool`:

Now let me run the tests again:

All 4 tests pass. Now let me also run the broader test suite to make sure I haven't broken anything else:

All router tests pass. Let me run a broader test to make sure no regression:

Let me look at the signatures consistency test:

The Doc string in the FastAPI and routing.py decorators differ. Let me make them consistent:

Let me run the full test suite again to confirm:

Let me verify that the `test_fastapi_cli` failure is pre-existing (unrelated to my changes):

Good, the `test_fastapi_cli` failure is pre-existing and unrelated to my changes. Let me restore my changes:

My changes are restored. Let me verify all tests pass again:

All 4 tests pass. Let me run the broader test suite once more to confirm no regressions:

All 2060 tests pass. Let me also update the graph as instructed by AGENTS.md:

Let me run the exact command requested by the user:

All 4 tests pass. Here's a summary of the changes:

## Summary

### `fastapi/applications.py`
- Added `default_response_model_exclude_none: bool = False` parameter to `FastAPI.__init__()` with documentation
- Stored as `self.default_response_model_exclude_none`
- Passed through to the internal `APIRouter` constructor
- Updated `FastAPI.add_api_route`, `FastAPI.api_route`, and all 8 HTTP method decorators (`get`, `put`, `post`, `delete`, `options`, `head`, `trace`, `patch`) so their `response_model_exclude_none` parameter accepts `Default(False)` as the default — using the existing `DefaultPlaceholder` sentinel pattern (mirroring `response_class` / `strict_content_type`).

### `fastapi/routing.py`
- Added `default_response_model_exclude_none: bool | DefaultPlaceholder = False` to `APIRouter.__init__()` (stored on the router, not wrapped in `Default()` so the resolution works via `get_value_or_default`)
- Updated `APIRouter.add_api_route`, `APIRouter.api_route`, and all 8 HTTP method decorators analogously
- In `add_api_route`, the value is resolved with `get_value_or_default(response_model_exclude_none, self.default_response_model_exclude_none)`. Because the router's default is a plain `bool` (e.g. `True` when FastAPI sets it), `get_value_or_default(Default(False), True)` correctly returns `True`.

### `tests/test_global_exclude_none.py` (new)
4 tests:
- `test_app_default_true_excludes_none` — `FastAPI(default_response_model_exclude_none=True)` + plain route: `None` fields are excluded
- `test_app_default_false_includes_none` — default `FastAPI()` + plain route: `None` fields are included
- `test_explicit_per_route_overrides_app_default` — on a `True`-default app, the plain route strips `None` and the explicit `response_model_exclude_none=False` route keeps them
- `test_explicit_per_route_true_with_default_false` — symmetric case on a `False`-default app

Result: **4 passed** for the new tests, and **2060 existing tests still pass** (the only failure, `tests/test_fastapi_cli.py`, was pre-existing and unrelated — it requires the `coverage` module which isn't installed).