The graphify command wasn't approved, so I'll read the source directly.

I couldn't make the change: permission to write to `fastapi/routing.py` was denied, and `graphify query` was also refused. So nothing has been modified, no test file exists, and pytest hasn't been run. I didn't try to get around the denial by writing through Bash.

Once you grant write access to `fastapi/routing.py` and `tests/`, this is what I'll apply:

**1. `APIRouter.__init__`**: add a new parameter right after `route_class` (around `fastapi/routing.py:2414`):
```python
route_class_map: Annotated[
    dict[str, type[APIRoute]] | None,
    Doc(
        """
        A mapping of HTTP methods (e.g. `"POST"`) to custom route
        (*path operation*) classes.

        When a route's HTTP method is a key in this mapping, that class is
        used instead of `route_class`. Other methods fall back to
        `route_class`.
        """
    ),
] = (None,)
```
and store it next to `self.route_class` (around line 2562), with the keys uppercased:
```python
        self.route_class_map: dict[str, type[APIRoute]] = {
            method.upper(): cls for method, cls in (route_class_map or {}).items()
        }
```

**2. `APIRouter.add_api_route`** (around line 2921):
```python
        route_class = route_class_override or self._get_route_class_for_methods(methods)
```
plus a helper method:
```python
def _get_route_class_for_methods(self, methods: set[str] | list[str] | None) -> type[APIRoute]:
    for method in methods or ["GET"]:
        route_class = self.route_class_map.get(method.upper())
        if route_class is not None:
            return route_class
    return self.route_class
```

**3. `tests/test_route_class_map.py`**:
```python
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


class AuditedRoute(APIRoute):
    accessed: list[str] = []

    def get_route_handler(self) -> Callable[[Request], Any]:
        original_handler = super().get_route_handler()

        async def audited_handler(request: Request) -> Any:
            AuditedRoute.accessed.append(self.path)
            return await original_handler(request)

        return audited_handler


def test_route_class_map_applies_per_method():
    AuditedRoute.accessed.clear()
    router = APIRouter(route_class_map={"POST": AuditedRoute})

    @router.get("/items")
    def read_items():
        return []

    @router.post("/items")
    def create_item():
        return {}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/items").status_code == 200
    assert client.post("/items").status_code == 200
    assert AuditedRoute.accessed == ["/items"]
```

A few design choices to confirm:
- **Precedence:** an explicit `route_class_override` still wins over the map. This matters because `include_router` passes the included route's own class as an override, so routes from a child router keep their class.
- **Routes with several methods** (e.g. `methods=["GET", "POST"]`): the first method found in the map decides the class.
- **No method given:** the route is treated as `GET`, which is FastAPI's default.

After that I'll run `pytest -W ignore tests/test_route_class_map.py` and `graphify update .`. Should I go ahead once permissions are granted?