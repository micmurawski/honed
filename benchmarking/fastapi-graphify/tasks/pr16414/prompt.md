Fix repeated endpoint wrapping in included routers.

For example, this test should pass once it's done:

```python
from functools import wraps

import pytest

from fastapi import APIRouter, Depends, FastAPI, routing

from fastapi.responses import PlainTextResponse

from fastapi.testclient import TestClient

def test_failed_handler_construction_restores_context_and_can_retry():
    fail = False

    class CustomRoute(routing.APIRoute):
        def get_route_handler(self):
            if fail:
                raise RuntimeError("handler construction failed")
            return super().get_route_handler()

    router = APIRouter(route_class=CustomRoute)

    @router.get("/items")
    def read_items():
        return ["item"]

    app = FastAPI()
    app.include_router(router, prefix="/api")
    fail = True
    assert routing._effective_route_context_var.get() is None
    with pytest.raises(RuntimeError, match="handler construction failed"):
        app.openapi()
    assert routing._effective_route_context_var.get() is None

    fail = False
    assert "/api/items" in app.openapi()["paths"]
    assert routing._effective_route_context_var.get() is None
    with TestClient(app) as client:
        assert client.get("/api/items").json() == ["item"]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
