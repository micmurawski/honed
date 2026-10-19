Now I have enough context. Let me verify how `methods` is handled in `add_api_route` and confirm the surrounding code. Let me also peek at what AGENTS.md says about implementation patterns:

Now I have all the context I need. Let me make the changes to `routing.py`:

**Step 1**: Add `route_class_map` parameter to `APIRouter.__init__()`.

**Step 2**: Modify `add_api_route()` to use `route_class_map` when applicable.

Let me verify the changes look right by reading the relevant sections:

Now let me write the test file:

Now let's run the tests as specified in the task:

Both tests pass. Let me also confirm I haven't broken any of the existing related tests:

All 62 related tests pass. Let me also run a wider sanity check on any tests that touch the routing machinery:

All tests pass. Let me also keep the knowledge graph in sync per `AGENTS.md`:

The `graphify` CLI isn't installed in this environment, so I can't run `graphify update .` here, but everything else is complete. Here's a summary of what was changed:

## Summary

### 1. `fastapi/routing.py` — `APIRouter.__init__()` (around line 2415)

Added a new `route_class_map: dict[str, type[APIRoute]] | None = None` parameter, with a `Doc` matching the style used by neighbouring parameters. Stored it on the instance as `self.route_class_map = route_class_map`.

### 2. `fastapi/routing.py` — `APIRouter.add_api_route()` (around line 2939)

Replaced the single-line `route_class = route_class_override or self.route_class` with a three-tier selection:

- `route_class_override` (existing per-route escape hatch) takes priority and behaviour is unchanged.
- Otherwise, if `self.route_class_map` is set, the first element of `methods` (defaulting to `["GET"]` — matching what `APIRoute` itself does) is uppercased and looked up in the map. A hit picks that class; a miss falls through.
- Otherwise, fall back to `self.route_class`, exactly as before.

Using `next(iter(route_methods))` keeps the lookup deterministic regardless of whether `methods` was passed as a list or a set.

### 3. `tests/test_route_class_map.py` (new file)

- `AuditedRoute(APIRoute)` overrides `handle()` to append `self.path` to the class-level list `AuditedRoute.accessed` before delegating to `super().handle()`.
- A `FastAPI` app is wired up with `APIRouter(route_class_map={"POST": AuditedRoute})` containing `GET /items` and `POST /items`.
- `test_route_class_map_records_only_post` resets `AuditedRoute.accessed`, fires both requests, and asserts the list contains `/items` exactly once (the POST).
- `test_route_class_map_picked_classes_per_method` confirms the GET route is a plain `APIRoute` while the POST route is an `AuditedRoute`.

### Verification

`pytest -W ignore tests/test_route_class_map.py`:

```
tests/test_route_class_map.py ..                                         [100%]
============================== 2 passed in 0.37s ===============================
```

Adjacent routing tests (`test_custom_route_class`, `test_default_response_class_router`, `test_include_router_defaults_overrides`, `test_router_*`, etc., 131 tests total) all still pass, confirming the `route_class` fall-back behaviour is unchanged when `route_class_map` is `None`.