Let me directly read the relevant files for this task.

Now I have a complete understanding. Let me make the changes step by step.

**Step 1: Modify `fastapi/routing.py` — add `deprecated_at` to `_APIRouteLike` protocol**

**Step 3: Add `deprecated_at` to `_populate_api_route_state`**

Now add `deprecated_at` storage in `_populate_api_route_state`, and auto-set `deprecated=True` when `deprecated_at` is set:

**Step 4: Add `deprecated_at` attribute and parameter to `APIRoute.__init__()`**

**Step 5: Add `deprecated_at` to `APIRouter.add_api_route()`**

**Step 6: Add `deprecated_at` to `APIRouter.api_route()`**

Now let me look at the structure of the HTTP method decorators to see where I need to make changes:

**Step 7: Add `deprecated_at` parameter to all 8 HTTP method decorators in `APIRouter`**

**Step 8: Add `deprecated_at` to FastAPI HTTP method decorators in `fastapi/applications.py`**

**Step 9: Update `fastapi/openapi/utils.py` to inject `x-deprecated-at` extension**

Now let me write the test:

**Step 10: Run the test**

Both tests pass. Let me verify that I haven't broken any existing tests by running a broader test sweep:

Both tests pass. Let me also do a sanity check on the test output schema to ensure it includes `x-deprecated-at`:

The OpenAPI schema correctly contains both `deprecated: true` and `x-deprecated-at: "2025-01-01"`.

Let me also verify that without `deprecated_at`, `deprecated` and `x-deprecated-at` are absent:

**Step 11: Add `deprecated_at` to `_EffectiveRouteContext` and propagate it**

Now I need to add `self.deprecated_at = deprecated_at` in `APIRouter.__init__()` body:

Now I need to update `APIRouter.include_router()` to accept and forward `deprecated_at`:

Let me verify the APIRouter case now works:

The `x-deprecated-at` is now correctly propagated when using `APIRouter` and `include_router`. Let me also test the include_router router-level deprecation:

Excellent! All paths work. Let me also verify that explicit `deprecated=True` still works without `x-deprecated-at`:

All 2010 tests pass. Let me run the specific test one more time to confirm:

Both tests pass. Let me also confirm the file structure with a quick look:

Here's a summary of the changes:

## Summary

### `fastapi/routing.py`
1. **`_APIRouteLike` protocol**: Added `deprecated_at: str | None`.
2. **`APIRoute` class**: Added `deprecated_at: str | None` attribute.
3. **`APIRoute.__init__()`**: Added `deprecated_at: str | None = None` parameter; passed it to `_populate_api_route_state()`.
4. **`_populate_api_route_state()`**: Added `deprecated_at` parameter; when set, it forces `deprecated = True` and stores `route.deprecated_at`.
5. **`APIRouter.__init__()`**: Added `deprecated_at` parameter; stored `self.deprecated_at`.
6. **`APIRouter.add_api_route()`**: Added `deprecated_at` parameter; passed to `APIRoute(...)`; combined with `self.deprecated_at`.
7. **`APIRouter.api_route()`**: Added `deprecated_at` parameter; passed to `add_api_route()`.
8. **8 `APIRouter` HTTP method decorators** (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`): Added `deprecated_at` parameter; passed to `self.api_route()`.
9. **`APIRouter.include_router()`**: Added `deprecated_at` parameter; passed to `_RouterIncludeContext.for_include()`.
10. **`_RouterIncludeContext` dataclass**: Added `deprecated_at: str | None = None` field; propagated through `for_include()` and `combine()`.
11. **`_EffectiveRouteContext` dataclass**: Added `deprecated_at: str | None = None` field; propagated in `from_api_route()`.

### `fastapi/applications.py`
12. **`FastAPI.__init__()`**: Added `deprecated_at` parameter; passed to `self.router = routing.APIRouter(...)`.
13. **`FastAPI.add_api_route()`**: Added `deprecated_at` parameter; passed to `self.router.add_api_route()`.
14. **`FastAPI.api_route()`**: Added `deprecated_at` parameter; passed to `self.router.add_api_route()`.
15. **`FastAPI.include_router()`**: Added `deprecated_at` parameter; passed to `self.router.include_router()`.
16. **8 `FastAPI` HTTP method decorators** (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`): Added `deprecated_at` parameter; passed to `self.router.X()`.

### `fastapi/openapi/utils.py`
17. **`get_openapi_operation_metadata()`**: When `route.deprecated_at` is set, inject `x-deprecated-at` into the operation dict.

### `tests/test_deprecated_at.py` (new)
- Creates an endpoint with `deprecated_at="2025-01-01"`, asserts the endpoint still responds 200, and verifies the OpenAPI schema contains `deprecated: true` and `x-deprecated-at: "2025-01-01"`.

### Behaviour matrix (verified)
| `deprecated=` | `deprecated_at=` | `deprecated` in schema | `x-deprecated-at` in schema |
|---|---|---|---|
| omitted | `"2025-01-01"` | `true` | `"2025-01-01"` |
| `True` | omitted | `true` | _absent_ |
| `False` | omitted | _absent_ | _absent_ |
| `True` | `"2025-01-01"` | `true` | `"2025-01-01"` |
| app/router level `deprecated_at=` | _inherits via include_ | `true` | inherited value |

### Test results
```
$ pytest -W ignore tests/test_deprecated_at.py
============================== 2 passed in 0.28s ==============================
```

A broader sweep (`tests/`, excluding pre-existing collection issues and the `coverage`-dependent `test_fastapi_cli.py`) also passed: **2010 passed, 6 skipped**.