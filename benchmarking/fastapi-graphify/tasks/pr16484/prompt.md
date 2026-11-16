Fix included route metadata in `iter_route_contexts()`.

For example, this test should pass once it's done:

```python
import threading

from collections.abc import Sequence

from typing import Annotated, cast

import pytest

from fastapi import APIRouter, Body, Depends, FastAPI, Request, Security

from fastapi.exceptions import FastAPIError

from fastapi.openapi.utils import get_openapi

from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from fastapi.routing import (
    APIRoute,
    APIWebSocketRoute,
    RouteContext,
    _IncludedRouter,
    _iter_included_route_candidates,
    _restore_fastapi_scope_key,
    iter_route_contexts,
)

from fastapi.security import HTTPBearer

from fastapi.testclient import TestClient

from pydantic import BaseModel

from starlette.routing import (
    BaseRoute,
    Host,
    Match,
    Mount,
    NoMatchFound,
    Route,
    Router,
    WebSocketRoute,
)

def test_iter_route_contexts_supports_nested_repeated_live_websocket_routes():
    router = APIRouter(prefix="/stream", dependencies=[Depends(dependency_b)])
    parent = APIRouter(prefix="/parent")
    parent.include_router(router, prefix="/child")
    app = FastAPI(openapi_url=None)
    for prefix in ["/v1", "/v2"]:
        app.include_router(parent, prefix=prefix, dependencies=[Depends(dependency_a)])

    # Populate the inclusion caches before adding the endpoint.
    assert list(iter_route_contexts(app.routes)) == []

    @router.websocket("/{job_id}")
    async def stream():  # pragma: no cover
        pass

    contexts = list(iter_route_contexts(app.routes))

    assert [context.path for context in contexts] == [
        "/v1/parent/child/stream/{job_id}",
        "/v2/parent/child/stream/{job_id}",
    ]
    for context in contexts:
        assert context.original_route is router.routes[0]
        assert [dependency.dependency for dependency in context.dependencies] == [
            dependency_a,
            dependency_b,
        ]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
