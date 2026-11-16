Fix frontend fallback support for doted paths like `/users/john.doe`.

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

@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/dashboard/settings", "text/html"),
        ("/users/jane.doe", "text/html"),
        ("/v/1.2.3", "application/xhtml+xml"),
    ],
)
def test_index_fallback_for_navigation_request(tmp_path: Path, path: str, accept: str):
    dist = tmp_path / "dist"
    write_file(dist / "index.html", "app shell")
    app = FastAPI()
    app.frontend("/", directory=dist, fallback="index.html")

    response = TestClient(app).get(path, headers={"accept": accept})

    assert response.status_code == 200
    assert response.text == "app shell"
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
