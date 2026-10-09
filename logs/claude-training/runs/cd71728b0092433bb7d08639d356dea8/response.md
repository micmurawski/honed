The `graphify` command needs approval I don't have in this session, so I'm going straight to the specific lines I need to change.

That's include_router; the HTTP-method decorators start after it. Let me look at one of them.

Those are Starlette routes, so I'll leave them alone. Now I'll apply the repetitive edits with a script and check the match counts.

Script files and running Python both need approval I don't have here. I'll try making the edits directly.

I couldn't make any changes. This session runs without permission to write or edit files or to run `python` and `pytest`, so every write and test run was refused. Nothing in the worktree has changed, and the tests have not been run. The project's `graphify query` step was also refused, so I read the relevant files directly instead.

Here is the change ready to apply. If you re-run with edit and Bash permissions (for example, allow `Edit`, `Write` and `Bash(pytest:*)`), I'll apply it and run the tests.

## Planned changes

**`fastapi/routing.py`**
- Add `include_in_schema_if: Callable[[], bool] | None` to:
  - the `_APIRouteLike` protocol (~line 933);
  - `_populate_api_route_state` (signature and `route.include_in_schema_if = ...`, ~line 984/1008);
  - `APIRoute.__init__` (~line 1184), passed through to `_populate_api_route_state`.
- Add the field to `_EffectiveRouteContext` (~line 1392) and pass `route.include_in_schema_if` along in `from_api_route` (~line 1455). Without this, routes inside an included router would silently lose the condition, because OpenAPI is built from these context objects.
- Add the parameter to `APIRouter.add_api_route` and `api_route` and to the `get/put/post/delete/options/head/patch/trace` decorators, documented with `Annotated[..., Doc(...)]` like the existing parameters.

**`fastapi/applications.py`**
- Add the same parameter to `FastAPI.add_api_route`, `api_route` and the eight HTTP-method decorators, forwarded to `self.router`.

**`fastapi/openapi/utils.py`**
- Add a helper and use it in place of `route.include_in_schema` in `get_openapi_path` (line 331) and `get_fields_from_routes` (line 562):
  ```python
  def _is_route_in_schema(route: routing._APIRouteLike) -> bool:
      if not route.include_in_schema:
          return False
      if route.include_in_schema_if is not None:
          return bool(route.include_in_schema_if())
      return True
  ```

**Decision for you:** `include_in_schema=False` still wins over the callable, so a router-level exclusion can't be overridden by a route's condition. With the default `include_in_schema=True`, the behaviour is exactly as you specified. If you'd rather the callable always decide, the helper is a one-line change.

**`tests/test_include_in_schema_if.py`**
```python
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

flag = {"enabled": lambda: False}

app = FastAPI()


@app.get("/public")
def public():
    return {"ok": True}


@app.get("/feature-flag", include_in_schema_if=lambda: flag["enabled"]())
def feature_flag():
    return {"ok": True}


client = TestClient(app)


def test_conditional_route_hidden_then_shown():
    app.openapi_schema = None
    paths = client.get("/openapi.json").json()["paths"]
    assert "/public" in paths
    assert "/feature-flag" not in paths

    with patch.dict(flag, {"enabled": lambda: True}):
        app.openapi_schema = None
        paths = client.get("/openapi.json").json()["paths"]
        assert "/public" in paths
        assert "/feature-flag" in paths
    app.openapi_schema = None
```

**Schema caching:** `FastAPI.openapi()` builds the schema once and saves it in `app.openapi_schema`. So the condition is checked when the schema is first built, not on every request, and the test has to clear `app.openapi_schema` after changing the flag. If you want the flag checked on every `/openapi.json` request, `openapi()` would need to stop caching whenever any route has a condition. That's a separate change, and I can add it if you want.