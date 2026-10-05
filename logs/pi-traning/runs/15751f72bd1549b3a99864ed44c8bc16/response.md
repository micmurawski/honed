All changes complete. Summary:

## Changes to `fastapi/routing.py`

Three coordinated edits inside `APIRouter`:

1. Added `route_class_map: dict[str, type[APIRoute]] | None = None` parameter to `__init__()` (right after `route_class`), with a docstring describing the per-method override behaviour.
2. Stored it on the instance: `self.route_class_map = route_class_map` (next to `self.route_class`).
3. In `add_api_route()`, after `route_class = route_class_override or self.route_class`, added:
   ```python
   if route_class_override is None and self.route_class_map and methods:
       for method in methods:
           method_upper = method.upper()
           if method_upper in self.route_class_map:
               route_class = self.route_class_map[method_upper]
               break
   ```
   This preserves existing behaviour — `route_class_override` still wins, and an explicit `route_class` still applies for methods not in the map.

## New test file: `tests/test_route_class_map.py`

- `AuditedRoute(APIRoute)` overrides `get_route_handler()` and appends `request.url.path` to `AuditedRoute.accessed` (class-level list).
- Builds `APIRouter(route_class_map={"POST": AuditedRoute})` with `@router.get("/items")` and `@router.post("/items")`.
- Four tests:
  - `test_get_does_not_trigger_audited_route` — GET leaves `accessed` empty.
  - `test_post_uses_audited_route` — POST records `"/items"`.
  - `test_only_post_uses_audited_route` — combined run, `accessed` contains `"/items"` exactly once (matches spec requirement).
  - `test_router_route_classes` — `type(get_route) is APIRoute`, `type(post_route) is AuditedRoute`.

## Test results

```
$ pytest -W ignore tests/test_route_class_map.py
tests/test_route_class_map.py ....                                       [100%]
============================== 4 passed in 0.25s ===============================
```

Also verified that `tests/test_custom_route_class.py` and other router-related test files still pass (60 tests total in the related suite) — existing `route_class` behaviour is unchanged.

Graph also refreshed with `graphify update .` per project conventions.