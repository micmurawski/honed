The graphify query was not approved, so I'll search the source directly.

This codebase has an unusual routing architecture (`_IncludedRouter`, `_EffectiveRouteContext`). Let me read the relevant parts.

I'm mirroring how `strict_content_type` already flows through the route, router, and include contexts, and adding `strict_slashes` alongside it. Making the edits now.

I couldn't make the changes because write access to `fastapi/routing.py` was denied. The `graphify` and multi-command shell calls were also blocked, so I read the source directly. Nothing has been modified, no test file was created, and the tests were not run.

Here is the patch I would apply once you grant write access. It follows the same path that `strict_content_type` already takes through this codebase's routing layer.

**`fastapi/routing.py`**

1. Add `import re`.
2. Add `strict_slashes: bool | DefaultPlaceholder` to the `_APIRouteLike` protocol (around line 939), to `APIRoute`'s attributes and `__init__` (~1145/1191, passed through to `_populate_api_route_state`), and to `_EffectiveRouteContext` as a field defaulting to `Default(True)` (~1402).
3. In `_populate_api_route_state`, add the parameter and loosen the compiled regex after `compile_path(path)` (line 1018). Both `APIRoute.matches()` and `_EffectiveRouteContext.matches()` use this regex, so it covers direct routes and included routes:
   ```python
   route.strict_slashes = strict_slashes
   ...
   route.path_regex, route.path_format, route.param_convertors = compile_path(path)
   strict = (
       strict_slashes.value
       if isinstance(strict_slashes, DefaultPlaceholder)
       else strict_slashes
   )
   if not strict:
       pattern = route.path_regex.pattern.removesuffix("$").removesuffix("/")
       route.path_regex = re.compile(pattern + "/?$")
   ```
   Because `/users/` now gets a full match on the first pass, `APIRouter.app()` never reaches its redirect branch.
4. In `_RouterIncludeContext`, add a `strict_slashes` field. Set it to `parent_router.strict_slashes` in `for_include()`, and use `get_value_or_default(child, self)` in `combine()`.
5. In `_EffectiveRouteContext.from_api_route()`, resolve the value as `get_value_or_default(route.strict_slashes, include_context.included_router.strict_slashes, include_context.strict_slashes)`.
6. In `APIRouter.__init__()`, add a documented `strict_slashes: Annotated[bool, Doc(...)] = Default(True)` and set `self.strict_slashes = strict_slashes`. I used `Default(True)` rather than a plain `True`, matching `strict_content_type`. It still acts as `True`, but a parent router can tell "not set" apart from an explicit `True`.
7. In `add_api_route()`, add `strict_slashes: bool | DefaultPlaceholder = Default(True)` and pass `strict_slashes=get_value_or_default(strict_slashes, self.strict_slashes)` to `route_class(...)`.

**`fastapi/applications.py`**: needs no change. `FastAPI.include_router()` only forwards to `self.router.include_router()`, and step 5 reads the included router's own `strict_slashes`, so the value carries through. The tests below cover this.

**`tests/test_strict_slashes.py`**
```python
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
loose = APIRouter(strict_slashes=False)
strict = APIRouter()


@loose.get("/users")
def users():
    return {"ok": True}


@strict.get("/users2")
def users2():
    return {"ok": True}


app.include_router(loose)
app.include_router(strict)
client = TestClient(app)


def test_non_strict_matches_both():
    assert client.get("/users").status_code == 200
    assert client.get("/users/", follow_redirects=False).status_code == 200


def test_strict_default():
    assert client.get("/users2").status_code == 200
    assert client.get("/users2/", follow_redirects=False).status_code in (307, 404)
```

To proceed, allow edits to `fastapi/routing.py` and creating the test file. I'll then apply the patch, run `pytest -W ignore tests/test_strict_slashes.py` and `graphify update .`, and tell you the results.