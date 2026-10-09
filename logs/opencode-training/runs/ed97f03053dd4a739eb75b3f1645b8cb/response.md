Now I have a clear understanding. Let me check the test setup once more and then start implementing:

Now I have a solid plan. Let me start implementing.

### Step 1: Modify `fastapi/routing.py`

#### 1.1. Add `rate_limit` to `_APIRouteLike` Protocol

#### 1.2. Update `_populate_api_route_state` to accept and store `rate_limit`

#### 1.3. Add `rate_limit` to the `APIRoute` class declaration and `__init__`

Now pass `rate_limit` through `_populate_api_route_state` in `APIRoute.__init__`:

#### 1.4. Add `rate_limit` to `APIRouter.add_api_route`

#### 1.5. Add `rate_limit` to `APIRouter.api_route`

#### 1.6. Add `rate_limit` to `APIRouter.get` decorator

Now I need to modify the remaining 7 HTTP method decorators in `APIRouter` (put, post, delete, options, head, patch, trace). Let me do them one at a time, but with a clean approach.

#### 1.7. Add `rate_limit` to `APIRouter.put` decorator

#### 1.8. Add `rate_limit` to `APIRouter.post` decorator

#### 1.9. Add `rate_limit` to `APIRouter.delete` decorator

#### 1.10. Add `rate_limit` to `APIRouter.options` decorator

#### 1.11. Add `rate_limit` to `APIRouter.head` decorator

I accidentally polluted the `head` docstring. Let me fix it:

#### 1.12. Add `rate_limit` to `APIRouter.patch` decorator

#### 1.13. Add `rate_limit` to `APIRouter.trace` decorator

Let me verify the routing.py changes are correct:

All 8 HTTP method decorators now have `rate_limit`. Now let me verify the integration works end-to-end:

I need to update `fastapi/applications.py` too. Let me start:

### Step 2: Update `fastapi/applications.py`

#### 2.1. Add `rate_limit` to `FastAPI.add_api_route`

#### 2.2. Add `rate_limit` to `FastAPI.api_route`

#### 2.3. Add `rate_limit` to `FastAPI.get` decorator

#### 2.4. Add `rate_limit` to `FastAPI.put` decorator

#### 2.5. Add `rate_limit` to `FastAPI.post` decorator

#### 2.6. Add `rate_limit` to `FastAPI.delete` decorator

#### 2.7. Add `rate_limit` to `FastAPI.options` decorator

#### 2.8. Add `rate_limit` to `FastAPI.head` decorator

#### 2.9. Add `rate_limit` to `FastAPI.patch` decorator

#### 2.10. Add `rate_limit` to `FastAPI.trace` decorator

Now let me verify the FastAPI changes are correct:

All the FastAPI decorators have the `rate_limit` parameter. Now let me also confirm the same for `APIRouter` decorators (`add_api_route`, `api_route`):

### Step 3: Update `fastapi/openapi/utils.py` to inject `x-rate-limit` into the OpenAPI operation

Now let me test the integration end-to-end:

The integration works. Now let me write the test file.

### Step 4: Write `tests/test_rate_limit_metadata.py`

### Step 5: Run the tests

Both tests pass. Now let me also run the existing test for `openapi route extensions` to make sure I didn't break anything:

Let me also run a few broader tests to ensure nothing else is broken:

All 24 OpenAPI tests pass. Now let me check that the APIRouter also propagates `rate_limit` properly. Let me also write a quick sanity test for the APIRouter path:

I found the issue. The `_EffectiveRouteContext.from_api_route` re-creates state from the original route when a router is included, but doesn't propagate `rate_limit`. Let me fix that:

Let me verify the fix:

Both `APIRouter` and `FastAPI` decorators now properly propagate `rate_limit`. Let me also verify a nested router case:

Nested routers also work. Let me run the test once more to confirm everything is good:

Both tests pass. Let me also re-run a broader set of related tests to make sure nothing is broken:

All 129 tests pass. Let me also run the test for the new file specifically:

Let me also view the final test file to confirm it matches the task requirements:

Let me also confirm the changes to `openapi/utils.py` look right:

Now let me update the knowledge graph as instructed in the AGENTS.md:

Now let me also do a final review by running both the new test and a broader test to make sure everything is working:

All 6 tests pass. Let me also run a quick summary of all the changes I made:

All the changes look correct. Let me run the full new test once more to confirm:

All tests pass. Here's a summary of the changes I made:

## Summary

### Files modified
1. **`fastapi/routing.py`** (~122 lines changed)
   - Added `rate_limit: str | None` to the `_APIRouteLike` Protocol
   - Added `rate_limit: str | None` declaration to the `APIRoute` class
   - Added `rate_limit: str | None = None` parameter and storage to `_populate_api_route_state()`
   - Added `rate_limit: str | None = None` parameter to `APIRoute.__init__()`, forwarded to `_populate_api_route_state()`
   - Added `rate_limit: str | None = None` parameter to `APIRouter.add_api_route()`, forwarded to `route_class(...)`
   - Added `rate_limit: str | None = None` parameter to `APIRouter.api_route()`, forwarded to `self.add_api_route()`
   - Added `rate_limit: str | None = None` (with `Annotated[..., Doc(...)]` docstring) to all 8 HTTP method decorators on `APIRouter` (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`)
   - Propagated `rate_limit=route.rate_limit` in `_EffectiveRouteContext.from_api_route()` so it survives `include_router` (including nested routers)

2. **`fastapi/applications.py`** (~108 lines changed)
   - Added `rate_limit: str | None = None` parameter to `FastAPI.add_api_route()`, forwarded to `self.router.add_api_route()`
   - Added `rate_limit: str | None = None` parameter to `FastAPI.api_route()`, forwarded to `self.add_api_route()`
   - Added `rate_limit: str | None = None` (with `Annotated[..., Doc(...)]` docstring) to all 8 HTTP method decorators on `FastAPI` (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`)

3. **`fastapi/openapi/utils.py`** (2 lines added)
   - In `get_openapi_path()`, after the existing `if route.openapi_extra:` block, inject `x-rate-limit` from `route.rate_limit` into the operation when set.

### File created
4. **`tests/test_rate_limit_metadata.py`** (31 lines)
   - Two tests that match the task spec exactly:
     - `test_openapi_includes_x_rate_limit_when_set` — asserts `paths["/limited"]["get"]["x-rate-limit"] == "50/minute"`
     - `test_openapi_omits_x_rate_limit_when_not_set` — asserts `"x-rate-limit" not in paths["/unlimited"]["get"]`

### Test results
- `pytest -W ignore tests/test_rate_limit_metadata.py` → 2 passed
- 129 existing related tests (`test_openapi_*`, `test_custom_route_class`, `test_router_include_context`, `test_generate_unique_id_function`, `test_application`, `test_include_route`, `test_include_router_defaults_overrides`) still pass — no regressions.
- Verified end-to-end with a quick smoke test that nested routers and `app.include_router()` also propagate `rate_limit` correctly into the OpenAPI spec.