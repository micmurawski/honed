Re-implement `on_event` in FastAPI for compatibility with the next Starlette, while keeping backwards compatibility.

For example, this test should pass once it's done:

```python
from collections.abc import AsyncGenerator

from contextlib import asynccontextmanager

from typing import Union

import pytest

from fastapi import APIRouter, FastAPI, Request

from fastapi.testclient import TestClient

from pydantic import BaseModel

def test_merged_no_return_lifespans_return_none() -> None:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    @asynccontextmanager
    async def router_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    router = APIRouter(lifespan=router_lifespan)
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)

    with TestClient(app) as client:
        assert not client.app_state
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
