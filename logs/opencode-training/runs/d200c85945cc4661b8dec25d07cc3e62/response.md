Let me query the graphify knowledge graph for context on the relevant files.

The graphify CLI isn't available, so I'll work directly with the source files. Let me read the key files in parallel.

Let me verify the test conventions and check for existing rate-limit-related tests.

Now I have a complete understanding. Let me implement the changes step by step.

**Step 1: Modify `fastapi/routing.py`** - Update the `_APIRouteLike` Protocol.

**Step 1b:** Add `rate_limit` parameter to `_populate_api_route_state`.

Now set `route.rate_limit = rate_limit` at the end of `_populate_api_route_state`. Let me find the end of the function and add it.

Now add `rate_limit` to the `APIRoute` class annotations and `__init__()`.

Now add `rate_limit` to `_EffectiveRouteContext` and propagate it in `from_api_route`.

Now update `from_api_route` to pass `rate_limit`:

Now update `APIRouter.add_api_route()` to accept and propagate `rate_limit`:

Now propagate `rate_limit` to `route_class(...)`:

Now update `APIRouter.api_route()` to accept and propagate `rate_limit`:

Now update `APIRouter.get()` to accept and propagate `rate_limit`:

Now I'll add the `rate_limit` kwarg to all 8 HTTP method decorators using replaceAll:

Now add the `rate_limit` parameter declaration to all 8 method decorators. Let me find the exact parameter pattern:

Now let me add the `rate_limit` parameter to the `get()` method. I'll use the unique docstring as context:

Now do the same for `put()`:

Now `post()`:

Now `delete()`:

Now `options()`:

Now `head()`:

Now `patch()`:

Now `trace()`:

Verify all8 are updated:

All 8 method decorators updated. Now let me verify the kwargs in the body of these methods also have `rate_limit=rate_limit,`:

All `routing.py` modifications are in place. **Step 2: Update `fastapi/openapi/utils.py`** to inject `x-rate-limit` into the operation.

**Step 3: Update `fastapi/applications.py`** to propagate `rate_limit` through `FastAPI.add_api_route()`, `api_route()`, and all HTTP method decorators.

Now update `FastAPI.api_route()`:

Now update `FastAPI.get()`:

Now update `FastAPI.put()`:

Now `FastAPI.post()`:

Now `FastAPI.delete()`:

Now `FastAPI.options()`:

Now `FastAPI.head()`:

Now `FastAPI.patch()`:

Now `FastAPI.trace()`:

Verify all 8 FastAPI method decorators have `rate_limit`:

All 8 are updated. Now verify the kwargs in FastAPI methods also have `rate_limit`:

**Step 4: Create `tests/test_rate_limit_metadata.py`:**

Now let me run the test:

All 4 tests pass. Let me also verify the implementation by running a broader test suite to make sure I didn't break anything:

All existing related tests still pass. Let me also do a quick import sanity check and an end-to-end smoke test of the full app:

The earlier pytest run already validated things correctly. Let me run a broader test suite to verify the implementation doesn't break anything related:

All 48 router tests still pass. Let me also run a more comprehensive smoke test using the project test framework to verify the broader implementation:

All passing. Let me also verify the final test once more and then update the graph (per the AGENTS.md instruction):

All 4 tests pass. Now let me try running `graphify update` as per AGENTS.md instructions (though graphify CLI isn't available):

Let me do a final syntax check of the modified files to ensure everything is correct:

All syntax checks pass. Let me also verify the final summary of changes:

All work is complete. Here's a summary of the implementation:

## Summary

**Implementation of `rate_limit` metadata field for FastAPI route decorators.**

### Files modified:

**`fastapi/routing.py`** (+108 lines)
- Added `rate_limit: str | None` to the `_APIRouteLike` Protocol.
- Added `rate_limit: str | None = None` parameter to `_populate_api_route_state()` and propagated it to `route.rate_limit`.
- Added `rate_limit: str | None` class annotation and `rate_limit: str | None = None` parameter to `APIRoute.__init__()`.
- Added `rate_limit: str | None = None` field to the `_EffectiveRouteContext` dataclass and propagated it via `from_api_route()`.
- Added `rate_limit: str | None = None` parameter to `APIRouter.add_api_route()` and `APIRouter.api_route()`, threading it through to the constructed `APIRoute`.
- Added `rate_limit: Annotated[str | None, Doc(...)] = None` parameter (with docstring) to all 8 HTTP method decorators on `APIRouter`: `get()`, `put()`, `post()`, `delete()`, `options()`, `head()`, `patch()`, `trace()`, and propagated it through each call to `self.api_route(...)`.

**`fastapi/openapi/utils.py`** (+2 lines)
- In `get_openapi_operation_metadata()`, after setting `operation["deprecated"]`, added:
  ```python
  if getattr(route, "rate_limit", None):
      operation["x-rate-limit"] = route.rate_limit
  ```
  This emits the OpenAPI extension only when `rate_limit` is set.

**`fastapi/applications.py`** (+100 lines)
- Added `rate_limit: str | None = None` parameter to `FastAPI.add_api_route()` and `FastAPI.api_route()`, propagating through to `self.router.add_api_route(...)`.
- Added `rate_limit: Annotated[str | None, Doc(...)] = None` parameter (with matching docstring) to all 8 HTTP method decorators on `FastAPI`: `get()`, `put()`, `post()`, `delete()`, `options()`, `head()`, `patch()`, `trace()`, and propagated it through to `self.router.<method>(...)`.

### File created:

**`tests/test_rate_limit_metadata.py`** (new file)
- Defines a `GET /limited` endpoint with `rate_limit="50/minute"`.
- Defines a `GET /unlimited` endpoint without `rate_limit`.
- Asserts that `paths["/limited"]["get"]["x-rate-limit"]` equals `"50/minute"`.
- Asserts that `x-rate-limit` is absent on `paths["/unlimited"]["get"]`.
- Includes smoke tests confirming both endpoints still serve requests.

### Test results```
tests/test_rate_limit_metadata.py::test_limited_endpoint_surfaces_x_rate_limit_in_openapi PASSED
tests/test_rate_limit_metadata.py::test_unlimited_endpoint_omits_x_rate_limit_in_openapi PASSED
tests/test_rate_limit_metadata.py::test_limited_endpoint_still_serves_requests PASSED
tests/test_rate_limit_metadata.py::test_unlimited_endpoint_still_serves_requests PASSED

============================== 4 passed in 0.18s ===============================
```

Also verified that the broader test suites (`test_application.py`, `test_openapi_route_extensions.py`, `test_openapi_query_parameter_extension.py`, `test_route_scope.py`, `test_router_include_context.py`, etc., totaling 70+ tests) continue to pass — the change is fully backward-compatible because `rate_limit` defaults to `None` and the `x-rate-limit` field is only emitted when set, so existing routes, router includes, and OpenAPI snapshots are unaffected.