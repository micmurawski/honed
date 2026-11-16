Add support for `app.frontend("/", directory="dist")` and `router.frontend("/", directory="dist")`.

For example, this test should pass once it's done:

```python
import errno

import os

import runpy

from pathlib import Path

import anyio

import pytest

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket

from fastapi.testclient import TestClient

from starlette.exceptions import HTTPException as StarletteHTTPException

from starlette.responses import PlainTextResponse, Response

from starlette.routing import BaseRoute, Match, NoMatchFound, Route

def test_check_dir_false_allows_missing_directory_and_fails_on_request(tmp_path: Path):
    app = FastAPI()
    app.frontend("/", directory=tmp_path / "missing", check_dir=False)

    with pytest.raises(RuntimeError, match="does not exist"):
        TestClient(app).get("/asset.txt")
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
