Add `iter_route_contexts()` for advanced use cases that used to use `router.routes` (e.g. Jupyverse).

For example, this test should pass once it's done:

```python
from typing import Annotated, cast

import pytest

from fastapi import APIRouter, Body, Depends, FastAPI, Request, Security

from fastapi.exceptions import FastAPIError

from fastapi.openapi.utils import get_openapi

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from fastapi.routing import (
    APIRoute,
    RouteContext,
    _IncludedRouter,
    _iter_included_route_candidates,
    _restore_fastapi_scope_key,
    iter_route_contexts,
)

from fastapi.security import HTTPBearer

from fastapi.testclient import TestClient

from pydantic import BaseModel

from starlette.routing import BaseRoute, Host, Match, Mount, NoMatchFound, Route, Router

def test_url_path_for_uses_effective_context_for_live_included_route():
    router = APIRouter()
    app = FastAPI()
    app.include_router(router, prefix="/api")

    @router.get("/items/{item_id}", name="read_item")
    def read_item(item_id: str):  # pragma: no cover
        return {"item_id": item_id}

    assert app.url_path_for("read_item", item_id="abc") == "/api/items/abc"
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
