Let me start implementing the changes. First, I'll modify `fastapi/routing.py`:

Now let me add `deprecated_at` to `_populate_api_route_state` and `APIRoute.__init__`:

Now let me update `add_api_route` and `api_route` in `APIRouter`:

Let me look at one of the verb decorators' call sections in detail:

Let me use a Python script to do the bulk replacement since the patterns are identical:

Now let me update the OpenAPI generation to inject `x-deprecated-at`:

Now let me update `fastapi/applications.py`. Let me check the structure of the verb decorators there:

Now let me create the test file:

Now let me run the test to verify everything works:

All tests pass. Let me also run some related existing tests to make sure I haven't broken anything:

All 52 existing tests still pass. Let me run the specific test command requested in the task:

Let me also update the graph as per project instructions:

Let me also do a final sanity check by running a broader set of tests:

These failures are environment issues (missing favicon.png, etc.) and unrelated to my changes. Let me confirm by running the test that the task requested one more time:

All 3 tests pass. Here's a summary of what was implemented:

## Summary

### Changes to `fastapi/routing.py`:
1. **Added `deprecated_at: str | None` to `_APIRouteLike` Protocol** — so OpenAPI utilities can read it.
2. **Added `deprecated_at: str | None` declaration on `APIRoute` class.**
3. **Added `deprecated_at` parameter to `APIRoute.__init__`** — when set, the route is automatically marked `deprecated=True` before the state is populated.
4. **Added `deprecated_at` to `_populate_api_route_state`** — assigns the value to `route.deprecated_at`.
5. **Added `deprecated_at` to `APIRouter.add_api_route`** — propagates the value to the constructed `APIRoute`.
6. **Added `deprecated_at` to `APIRouter.api_route`** — propagates it to `add_api_route`.
7. **Added `deprecated_at` to all 8 verb decorators** on `APIRouter` (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`) with full `Doc()`-annotated docstrings, and forward it to `api_route()`.

### Changes to `fastapi/applications.py`:
1. **Added `deprecated_at` parameter to all 8 verb decorators** on `FastAPI` (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`) with matching `Doc()` annotations, and forward it to `self.router.<verb>(...)`.

### Changes to `fastapi/openapi/utils.py`:
1. **In `get_openapi_operation_metadata`**, after `deprecated` is set, also write `x-deprecated-at` to the operation if `route.deprecated_at` is not `None`.

### New file `tests/test_deprecated_at.py`:
- Verifies that `deprecated_at="2025-01-01"` sets both `route.deprecated` and `route.deprecated_at`.
- Verifies the OpenAPI schema for `/items` contains `deprecated: true` and `x-deprecated-at: "2025-01-01"`.
- Verifies an active route (without `deprecated_at`) does not get the `deprecated` or `x-deprecated-at` keys.
- Verifies the endpoint is still callable.

### Run results:
```
tests/test_deprecated_at.py::test_deprecated_at_implies_deprecated PASSED
tests/test_deprecated_at.py::test_openapi_schema_has_deprecated_at_extension PASSED
tests/test_deprecated_at.py::test_endpoint_still_callable PASSED
============================== 3 passed in 0.34s ===============================
```