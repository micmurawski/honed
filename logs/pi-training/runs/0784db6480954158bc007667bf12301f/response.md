Now I have a clear picture. Let me start implementing the changes. First, I'll update the `_APIRouteLike` Protocol class and `APIRoute` in `routing.py`:

Now let's update `_populate_api_route_state` to accept and store `rate_limit`:

Now let's update the `_APIRouteLike` Protocol class:

Now let's update `APIRouter.add_api_route` to accept the `rate_limit` parameter:

Now let's update `APIRouter.api_route`:

Now let me update the HTTP verb methods in `routing.py` (Router). First, let me look at how `get`, `post`, `put`, etc. are structured:

Now I'll update each HTTP verb method. First, let me edit the `get` method:

Now I need to add the `rate_limit` parameter to each method's signature. Let me find the exact text for each method:

Now let me add the `rate_limit` parameter to each method's signature. The pattern is the same for all, distinguished by the docstring. Let me edit them:

Now let me update the `_EffectiveRouteContext` and `_RouterIncludeContext` dataclasses to support the `rate_limit` field:

Now let's update the OpenAPI utility to inject the `x-rate-limit` extension:

Now let's update `fastapi/applications.py` to add the `rate_limit` parameter to the HTTP verb methods:

Now I need to add `rate_limit=rate_limit` to each method's `self.router.XXX()` call. Let me find the unique pattern for each:

Let me check the current state of the file to see if the edits worked correctly:

I see the file has some duplicates. Let me check the duplicate `def post(`:

I need to remove the orphan code I accidentally introduced. Let me look at the broken area more carefully:

Let me check all the orphan code introduced:

Let me fix all 6 broken sections by replacing the orphan `def XXX(self,` with `        return self.router.XXX(`:

Let me revert my mistakes and start over:

Let me fix the broken edits:

Let me restore the file and redo this properly:

Now I'll properly edit each method by using the unique `return self.router.XXX(` line as the anchor:

Now I need to also add the `rate_limit` parameter to each method's signature. Let me do this carefully:

Now let me also add the `rate_limit` parameter to the `get` method's signature in applications.py:

Let me verify the changes are correct now:

Let me also verify `routing.py`:

Now let me write the test file:

Now let me run the test:

Both tests pass. Now let me also verify that I haven't broken anything else by running a broader test set:

All tests pass. Let me also do a final check on the routing.py to make sure everything is consistent and there are no other issues:

The functionality is working correctly. Let me also run a quick test to ensure all 8 HTTP verb methods accept the parameter properly:

All 8 HTTP verb methods correctly support the `rate_limit` parameter and surface it as `x-rate-limit` in the OpenAPI schema. Let me also run a broader test of the test suite to be safe:

Now let me update the graph:

Let me do a final summary of the changes:

Let me run the final test one more time to confirm everything is working:

All tests pass. Here's a summary of what I did:

## Summary

I added a `rate_limit` metadata field to FastAPI route decorators that gets surfaced in the OpenAPI spec as an `x-rate-limit` extension.

### Changes made

**`fastapi/routing.py`:**
- Added `rate_limit: str | None` to the `_APIRouteLike` Protocol class.
- Added `rate_limit: str | None` attribute to the `APIRoute` class.
- Added `rate_limit: str | None = None` parameter to `APIRoute.__init__()`.
- Added `rate_limit: str | None = None` parameter to `_populate_api_route_state()` and store it on the route via `route.rate_limit = rate_limit`.
- Added `rate_limit: str | None = None` to `APIRouter.add_api_route()` and pass it to the `APIRoute` constructor.
- Added `rate_limit: str | None = None` to `APIRouter.api_route()` and pass it to `add_api_route()`.
- Added the `rate_limit: Annotated[str | None, Doc(...)] = None` parameter to all 8 HTTP verb methods (`get`, `post`, `put`, `delete`, `patch`, `options`, `head`, `trace`) on `APIRouter`, and pass it through to `self.api_route()`.
- Added `rate_limit: str | None = None` to the `_EffectiveRouteContext` dataclass and propagated the field through `from_api_route()`.

**`fastapi/openapi/utils.py`:**
- In `get_openapi_operation_metadata`, inject `operation["x-rate-limit"] = route.rate_limit` when `route.rate_limit` is set.

**`fastapi/applications.py`:**
- Added the `rate_limit: Annotated[str | None, Doc(...)] = None` parameter to all 8 HTTP verb methods on `FastAPI` and pass it through to `self.router.<verb>()`.

**`tests/test_rate_limit_metadata.py`** (new file):
- Creates `GET /limited` with `rate_limit="50/minute"` and asserts the OpenAPI JSON contains `x-rate-limit: "50/minute"`.
- Creates `GET /unlimited` without `rate_limit` and asserts `x-rate-limit` is absent.

### Test results
- `pytest -W ignore tests/test_rate_limit_metadata.py` → **2 passed**.
- Sanity-checked the 8 HTTP verb methods (`get`, `post`, `put`, `delete`, `patch`, `options`, `head`, `trace`) and verified each correctly surfaces `x-rate-limit` in its OpenAPI operation.
- Verified existing related tests (`test_openapi_route_extensions.py`, `test_application.py`, `test_custom_route_class.py`, `test_default_response_class_router.py`, `test_empty_router.py`, `test_include_route.py`, `test_include_router_defaults_overrides.py`) still pass.
- Ran `graphify update .` to keep the knowledge graph current.