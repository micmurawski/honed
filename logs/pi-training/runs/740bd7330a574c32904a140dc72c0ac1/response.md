Now I have a clear picture. Let me look at the `AuditedRoute` requirements more carefully and then implement the changes.

Now I'll add the `route_class_map` parameter to `APIRouter.__init__` right after `route_class`:

Now let me update the body of `__init__` to store `route_class_map`:

Now let me update `add_api_route` to use the route_class_map:

Let me verify the changes:

Now let me check the new `__init__` signature to ensure it's correctly placed:

Now let me create the test file:

Now let me run the tests:

All 4 tests pass. Let me also run the related existing tests to make sure my changes don't break anything:

All 19 related existing tests still pass. Let me also run a broader sweep on routing-related tests:

All 28 routing-related tests pass, including the 4 new ones.

Summary of changes:

**`fastapi/routing.py`** — two changes:

1. **`APIRouter.__init__`**: added a new keyword-only `route_class_map: dict[str, type[APIRoute]] | None = None` parameter (with `Doc` annotation) right after `route_class`. The body stores it as `self.route_class_map`.

2. **`APIRouter.add_api_route`**: replaced the existing one-liner selection   ```python
   route_class = route_class_override or self.route_class
   ```
   with a three-tier priority:
   - `route_class_override` (per-route override, unchanged)
   - else, if `self.route_class_map` is set and `methods` is non-empty, look up each method (uppercased) in the map and use the first match
   - else, fall back to `self.route_class`

   The lookup is case-insensitive on keys (HTTP method names are normalised via `method.upper()`). When `methods` is `None` or no method matches a key, behaviour is identical to before.

**`tests/test_route_class_map.py`** — new test file that:
- Defines `AuditedRoute(APIRoute)` with a class-level `accessed: list[str] = []`, overriding `get_route_handler` to wrap the original handler and append `self.path` on each access.
- Creates `APIRouter(route_class_map={"POST": AuditedRoute})` with two handlers at `/items` (one GET, one POST) and a `TestClient`.
- Asserts: GET does not record anything, POST records `/items`, and after both requests `AuditedRoute.accessed == ["/items"]` (only the POST). Also asserts the GET route is a plain `APIRoute` while the POST route is an `AuditedRoute` instance.