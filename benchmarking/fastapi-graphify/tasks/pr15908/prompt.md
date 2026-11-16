Support dependencies in `app.frontend()`, e.g. for automatic cookie authentication for the frontend.

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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket

from fastapi.testclient import TestClient

from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.responses import PlainTextResponse, Response

from starlette.routing import BaseRoute, Match, NoMatchFound, Route

def test_frontend_dependency_overrides_apply(tmp_path: Path):
    calls: list[str] = []

    def require_cookie() -> None:
        raise HTTPException(status_code=401)  # pragma: no cover

    def allow_cookie() -> None:
        calls.append("override")

    dist = tmp_path / "dist"
    write_file(dist / "index.html", "app")
    app = FastAPI(dependencies=[Depends(require_cookie)])
    app.dependency_overrides[require_cookie] = allow_cookie
    app.frontend("/", directory=dist)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.text == "app"
    assert calls == ["override"]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
