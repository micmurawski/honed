Now let me look at how the APIRoute constructor handles parameters:

Now let me look at the FastAPI class to understand how it inherits from APIRouter:

Let me look at the DefaultPlaceholder usage:

Now let me look at the imports and overall structure of APIRouter:

Now I have enough context to implement the changes. Let me start with the implementation.

**Step 1: Update `_APIRouteLike` protocol in routing.py to add `strict_slashes` attribute.**

**Step 2: Update `_populate_api_route_state` to accept and store `strict_slashes`.**

**Step 3: Add `strict_slashes` parameter to `APIRoute.__init__()`.**

**Step 4: Update `APIRouter.__init__()` to accept `strict_slashes`.**

Now let's update the body of `APIRouter.__init__()` to set `self.strict_slashes` and to disable Starlette's `redirect_slashes` when `strict_slashes=False`:

**Step 5: Update the `app()` method in APIRouter to dispatch to alternate-slash routes when `strict_slashes=False`.**

**Step 6: Update `APIRouter.add_api_route()` to accept and propagate `strict_slashes`.**

Now let me verify the changes I made to `routing.py`:

**Step 7: Add `strict_slashes` to `_RouterIncludeContext` and `_EffectiveRouteContext`.**

Now update `for_include` to set the `strict_slashes` value:

Now update `_EffectiveRouteContext`:

**Step 8: Propagate `strict_slashes` through `FastAPI.include_router()`.**

**Step 9: Update `APIRouter.include_router()` to accept `strict_slashes`.**

Let me check the actual structure and make the right changes to propagate `strict_slashes`:

Now update the `for_include` call:

Now update `_RouterIncludeContext.for_include` to accept and use the parameter:

Now update `FastAPI.include_router` to use `DefaultPlaceholder`:

Good, imports are already in place. Now let me also do a basic syntax check by importing the module:

Now let me run the existing tests to make sure I haven't broken anything:

Now let me create the test file:

Let me look at the FastAPI class setup method:

Let me look at the `_IncludedRouter` class:

Let me look at the relevant code in the APIRouter's `app()` method:

I need to update the parent router's `app()` method to also check the included router's `strict_slashes` setting. Let me modify the redirect logic:

Now let me run the tests again:

I need to update the scope's path to the alternate path before dispatching:

All 4 tests pass. Now let me run the full test suite to make sure I haven't broken anything:

Only one failure unrelated to my changes (missing `coverage` module). Let me also verify all routing-related tests pass:

All routing and application tests pass. Now let me also check a few more potential edge cases. Let me also test with prefix:

Now let me also test a more complex case to be sure:

Let me also test the second scenario more explicitly:

Both expected behaviors work. Let me also check the test runs in the way requested by the task:

All 4 tests pass. Now let me also run a broader subset of tests to ensure no regressions:

All 2041 tests pass with no failures. Let me also do a quick visual check of the final state of the routing.py changes:

Let me verify the final state of the changes by looking at the key sections:

Now let me look at the matching logic in the parent router:

Let me test that the strict_slashes on FastAPI itself works too:

Now let me run the exact command requested by the task:

All 4 tests pass. Let me also view the final test file to verify the tests are complete:

All changes are complete. Let me do a final verification by running the exact test command from the task:

All 4 tests pass. Here's a summary of what was implemented:

## Summary

I added `strict_slashes` support to FastAPI's `APIRouter` so that routes can match both `/items` and `/items/` without redirecting when configured.

### Changes to `fastapi/routing.py`

1. **`_APIRouteLike` protocol**: Added `strict_slashes: bool | DefaultPlaceholder` attribute.

2. **`_populate_api_route_state()`**: Added `strict_slashes: bool | DefaultPlaceholder = Default(True)` parameter and store it on the route.

3. **`APIRoute.__init__()`**: Added `strict_slashes: bool | DefaultPlaceholder = Default(True)` parameter and pass it to `_populate_api_route_state()`.

4. **`APIRouter.__init__()`**: Added `strict_slashes: bool = True` parameter. When `False`, Starlette's `redirect_slashes` is forced to `False`. Stored as `self.strict_slashes`.

5. **`APIRouter.app()` method**: 
   - When `strict_slashes=False`, try matching the alternate slash form and dispatch directly (no redirect).
   - When `redirect_slashes=True` (default), check if a matching route is an `_IncludedRouter` whose original router has `strict_slashes=False`. If so, dispatch directly with the alternate path.

6. **`APIRouter.add_api_route()`**: Added `strict_slashes: bool | DefaultPlaceholder = Default(True)` and propagate to `APIRoute` using `get_value_or_default`.

7. **`APIRouter.include_router()`**: Added `strict_slashes: bool | DefaultPlaceholder = Default(True)` parameter.

8. **`_RouterIncludeContext`**: Added `strict_slashes` field; updated `for_include()` and `combine()` to propagate it. By default uses the included router's value.

9. **`_EffectiveRouteContext`**: Added `strict_slashes` field; updated `from_api_route()` to propagate it.

### Changes to `fastapi/applications.py`

10. **`FastAPI.include_router()`**: Added `strict_slashes: bool | DefaultPlaceholder = Default(True)` parameter and pass it to `self.router.include_router()`.

### New file `tests/test_strict_slashes.py`

Four tests covering:
- `test_strict_slashes_false_both_forms_return_200`: The primary feature — both `/users` and `/users/` return 200 with `strict_slashes=False`.
- `test_strict_slashes_default_preserves_redirect_behavior`: The default redirect behavior is preserved (`/users2` returns 307).
- `test_strict_slashes_propagates_to_api_route`: The router's `strict_slashes=False` propagates to individual `APIRoute` instances.
- `test_strict_slashes_default_on_api_route`: Default `strict_slashes=True` on `APIRoute` when router uses default.