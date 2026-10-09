The graphify command needs approval that I can't get in this session, so I'll search the files directly.

I've traced the full call chain and am now applying the edits with a script, since about 20 near-identical signatures need changing.

Writing new files is blocked in this session, so I'll make the edits directly with the Edit tool. First, the core route state in `routing.py`:

I couldn't make any of the changes: this session has no write permission. Every edit to `fastapi/routing.py` was refused, and so were attempts to create a helper script in the worktree or in `/tmp`. `graphify query` also needs approval, so I located the code with grep and file reads instead. Nothing was modified, no test file was created, and I couldn't run pytest.

If you grant write access to the worktree (Edit/Write on `fastapi/` and `tests/`), I can apply this plan:

**How a request reaches the route:** `@app.get` calls `FastAPI.get`, which calls `APIRouter.get`, then `APIRouter.api_route`, then `APIRouter.add_api_route`, then `APIRoute.__init__`. That calls `_populate_api_route_state` (`fastapi/routing.py:961`). When a router is included in another, its routes are rebuilt through `_EffectiveRouteContext.from_api_route` (`fastapi/routing.py:1426`).

**Changes in `fastapi/routing.py`:**
- Add `deprecated_at: str | None` to `_APIRouteLike`, `APIRoute` and the `_EffectiveRouteContext` dataclass (default `None` there).
- In `_populate_api_route_state`, add the parameter and set:
  ```python
  route.deprecated_at = deprecated_at
  # a deprecation date implies the route is deprecated
  route.deprecated = True if deprecated_at is not None else deprecated
  ```
- Pass `deprecated_at` through `APIRoute.__init__`, `add_api_route`, `api_route`, the eight HTTP-method decorators (`get` … `trace`), and `from_api_route` (`deprecated_at=route.deprecated_at`).
- Leave `APIRouter(...)`, `include_router` and the include context alone. Their `deprecated` flag applies to every route in a router, and a per-endpoint date doesn't make sense there.

**Changes in `fastapi/applications.py`:** add the same parameter to `add_api_route`, `api_route` and `get` … `trace`, but not to `FastAPI.__init__` or `include_router`.

**Change in `fastapi/openapi/utils.py`** (in `get_openapi_operation_metadata`, at line 306):
```python
if route.deprecated_at:
    operation["x-deprecated-at"] = route.deprecated_at
```
This puts `x-deprecated-at` directly on the operation, next to `deprecated: true`. An `info` extension only exists at the document level in OpenAPI, so I read step 3 as meaning the operation itself.

**New file `tests/test_deprecated_at.py`:** a `@app.get("/old", deprecated_at="2025-01-01")` endpoint and a normal endpoint. It checks that `/old` has `deprecated: true` and `x-deprecated-at: "2025-01-01"`, and that the normal endpoint has neither. It also checks that the date still appears when the route comes from an included `APIRouter`.

One compatibility issue: `add_api_route` will always pass `deprecated_at=` to `route_class`. A custom `APIRoute` subclass whose `__init__` lists its parameters explicitly, without `**kwargs`, would then raise a `TypeError`. Adding new parameters this way, as was done for `strict_content_type`, has the same effect. If that matters, I can pass the argument only when it's set.

After applying the changes I would run `pytest -W ignore tests/test_deprecated_at.py` and `graphify update .`.