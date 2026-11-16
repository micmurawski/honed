Isolate FastAPI telemetry for excluded requests.

For example, this test should pass once it's done:

```python
import asyncio

import gc

import weakref

import anyio

import pytest

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket

from fastapi.telemetry import get_telemetry_data

from fastapi.testclient import TestClient

from httpx import ASGITransport, AsyncClient

from opentelemetry import baggage, context

from opentelemetry._logs import SeverityNumber

from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor

from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

from pydantic import BaseModel

from starlette.websockets import WebSocketDisconnect

from ._otlp import otlp_collector

from .conftest import metric_points, server_spans

@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [ValueError, asyncio.CancelledError])
async def test_excluded_request_restores_context_after_error(
    telemetry, logs, error_type
):
    config, exporter, _ = telemetry
    app = FastAPI(
        telemetry={**config, "exclude": lambda scope: scope["path"] == "/inner"}
    )
    error = error_type()

    @app.get("/inner")
    async def inner():
        assert get_telemetry_data() is None
        raise error

    @app.get("/outer")
    async def outer(request: Request):
        outer_context = context.get_current()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://internal"
        ) as client:
            with pytest.raises(error_type) as caught:
                await client.get("/inner")
        assert caught.value is error
        assert context.get_current() is outer_context
        data = get_telemetry_data()
        assert data is not None
        assert data.request is request
        return "ok"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/outer")).json() == "ok"
    (span,) = server_spans(exporter)
    assert span.name == "GET /outer"
    assert not logs.get_finished_logs()
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
