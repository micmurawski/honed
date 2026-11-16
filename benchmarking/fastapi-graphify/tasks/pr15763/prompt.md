Fix bug, allow empty path in path operation in prefixless router.

For example, this test should pass once it's done:

```python
from typing import Annotated, cast

import pytest

from fastapi import APIRouter, Body, Depends, FastAPI, Request

from fastapi.exceptions import FastAPIError

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from fastapi.routing import (
    APIRoute,
    _IncludedRouter,
    _iter_included_route_candidates,
    _restore_fastapi_scope_key,
)

from fastapi.testclient import TestClient

from starlette.routing import BaseRoute, Host, Match, Mount, NoMatchFound, Route, Router

def test_no_prefix_include_validation_sees_effective_api_route_path():
    leaf_router = APIRouter()

    @leaf_router.get("")
    def read_items():
        return []

    parent_router = APIRouter()
    parent_router.include_router(leaf_router, prefix="/items")

    # for coverage
    candidates = list(_iter_included_route_candidates(parent_router.routes))
    assert cast(APIRoute, candidates[0]).path == ""

    app = FastAPI()
    app.include_router(parent_router)
    client = TestClient(app)

    response = client.get("/items")

    assert response.status_code == 200, response.text
    assert response.json() == []
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
