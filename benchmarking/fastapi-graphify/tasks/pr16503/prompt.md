Fix blocking frontend fallback file checks.

For example, this test should pass once it's done:

```python
import asyncio

import errno

import os

import runpy

from contextlib import AsyncExitStack

from pathlib import Path

from typing import Literal

import anyio

import pytest

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
)

from fastapi.testclient import TestClient

from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.responses import PlainTextResponse, Response

from starlette.routing import BaseRoute, Match, NoMatchFound, Route

def test_auto_fallback_uses_index_when_404_is_missing(monkeypatch, tmp_path: Path):
    dist = tmp_path / "dist"
    write_file(dist / "index.html", "app shell")
    app = FastAPI()
    app.frontend("/", directory=dist)
    frontend_routes = app.router._frontend_routes
    assert frontend_routes is not None
    static_files = frontend_routes.routes[0].app
    original_lookup_path = static_files.lookup_path

    def lookup_path(path: str):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        return original_lookup_path(path)

    monkeypatch.setattr(static_files, "lookup_path", lookup_path)

    response = TestClient(app).get("/dashboard", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.text == "app shell"
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
