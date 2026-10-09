I'm going to add `rate_limit` everywhere `openapi_extra` is already passed through, using one script so all 20+ signatures get the same edit.

I couldn't make any of the changes. This session isn't allowed to write files: Write and Edit were refused, the same block applied to a helper script in `/tmp` and in the worktree, and the `python` and `graphify query` commands also needed approval I don't have. Nothing in the repo was modified and no tests were run.

I did read the code first. `openapi_extra` already travels the whole path from the decorator to the OpenAPI output, so `rate_limit` can be added right next to it at every step:

**`fastapi/routing.py`**
- **Attribute declarations:** add `rate_limit: str | None` to the `_APIRouteLike` protocol (~line 937) and the `APIRoute` class (~1143). Add `rate_limit: str | None = None` to the `_EffectiveRouteContext` dataclass (~1398).
- **Shared setup function:** add the parameter to `_populate_api_route_state` (~988) and store it with `route.rate_limit = rate_limit` (~1012).
- **`APIRoute.__init__`** (~1188): accept `rate_limit` and pass it to `_populate_api_route_state`.
- **`_EffectiveRouteContext.from_api_route`** (~1465): pass `rate_limit=route.rate_limit`. Without this, routes added through `include_router` would lose the value in the spec.
- **`APIRouter`:** add the parameter to `add_api_route` (~2916), which passes it to `route_class(...)`, and to `api_route` (~2998). Then add it to `get`, `put`, `post`, `delete`, `options`, `head`, `patch` and `trace`, each with a `Doc(...)` description like the one on `openapi_extra`.

**`fastapi/applications.py`**
- The same additions to `add_api_route` (~1190), `api_route` (~1325) and the 8 method decorators, passed through to the router.

**`fastapi/openapi/utils.py`** (~537)
```python
if route.rate_limit:
    operation["x-rate-limit"] = route.rate_limit
if route.openapi_extra:
    deep_dict_update(operation, route.openapi_extra)
```
Putting it before `openapi_extra` means a user can still override it with `openapi_extra={"x-rate-limit": ...}`.

**`tests/test_rate_limit_metadata.py`**: `GET /limited` with `rate_limit="50/minute"` and a second endpoint without one. The test fetches `/openapi.json`, checks that `paths["/limited"]["get"]["x-rate-limit"] == "50/minute"`, and checks that the key is missing on the other endpoint.

To let me finish, re-run this in an interactive session or allow edits for this worktree (for example with `/permissions` or `acceptEdits` mode). Allowing `python` and `graphify` lets me apply the signature changes with a script and keep the graph updated. After that I'll make the edits, run `pytest -W ignore tests/test_rate_limit_metadata.py` plus the existing OpenAPI tests, and run `graphify update .`.

Separately, the Gmail, Google Calendar and Google Drive connectors need to be authorized in your claude.ai connector settings before they can be used. This task doesn't need them.