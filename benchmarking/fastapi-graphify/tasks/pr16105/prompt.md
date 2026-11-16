Fix support for background tasks and headers from dependencies in `app.frontend()`.

For example, this test should pass once it's done:

```python
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

def test_frontend_dependency_response_headers_and_background_tasks(tmp_path: Path):
    calls: list[str] = []

    def frontend_dependency(
        response: Response, background_tasks: BackgroundTasks
    ) -> None:
        response.headers["X-Frontend-Dependency"] = "applied"
        response.set_cookie("frontend", "dependency")
        background_tasks.add_task(calls.append, "background")

    dist = tmp_path / "dist"
    write_file(dist / "index.html", "app")
    app = FastAPI(dependencies=[Depends(frontend_dependency)])
    app.frontend("/", directory=dist)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.text == "app"
    assert response.headers["X-Frontend-Dependency"] == "applied"
    assert response.cookies["frontend"] == "dependency"
    assert calls == ["background"]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
