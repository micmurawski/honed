Make `app.frontend()` return 404 for methods other than `GET` or `HEAD` with no static file matches.

For example, this test should pass once it's done:

```python
import errno

import os

import runpy

from pathlib import Path

from typing import Literal

import anyio

import pytest

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket

from fastapi.testclient import TestClient

from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.responses import PlainTextResponse, Response

from starlette.routing import BaseRoute, Match, NoMatchFound, Route

def test_unsupported_method_to_directory_without_index_returns_404(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "empty").mkdir(parents=True)
    write_file(dist / "index.html", "app")
    app = FastAPI()
    app.frontend("/", directory=dist)

    response = TestClient(app).post("/empty/")

    assert response.status_code == 404
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
