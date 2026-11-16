Refactor router route building to make it thread-safe, mainly relevant for tests running in parallel threads (uncommon).

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

def test_included_router_low_priority_cache_rechecks_version_after_lock(monkeypatch):
    router = APIRouter()
    app = FastAPI()
    app.include_router(router)
    included_router = cast(_IncludedRouter, app.router.routes[-1])
    routes_version = router._get_routes_version()
    version_checked = threading.Event()

    def get_routes_version() -> int:
        version_checked.set()
        return routes_version

    monkeypatch.setattr(router, "_get_routes_version", get_routes_version)
    result: list[Sequence[object]] = []

    def build_low_priority_routes() -> None:
        result.append(included_router.effective_low_priority_routes())

    with included_router._effective_routes_lock:
        thread = threading.Thread(target=build_low_priority_routes)
        thread.start()
        assert version_checked.wait(timeout=1)
        included_router._effective_low_priority_routes_version = routes_version
    thread.join()

    assert result == [[]]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
