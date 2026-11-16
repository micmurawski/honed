Cache OpenTelemetry tracers to preserve warning deduplication.

For example, this test should pass once it's done:

```python
import warnings

from unittest.mock import Mock

import pytest

from fastapi import FastAPI, WebSocket

from fastapi.testclient import TestClient

from opentelemetry import trace

from opentelemetry.sdk.trace import TracerProvider

from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .conftest import server_spans

@pytest.mark.parametrize("global_provider", [False, True])
@pytest.mark.parametrize("protocol", ["http", "websocket"])
def test_tracer_lookup_preserves_warning_deduplication(
    telemetry, monkeypatch, global_provider, protocol
):
    config, exporter, _ = telemetry
    provider = config["tracer_provider"]
    get_tracer = Mock(wraps=provider.get_tracer)
    monkeypatch.setattr(provider, "get_tracer", get_tracer)
    if global_provider:
        monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
        config["tracer_provider"] = None
    app = FastAPI(telemetry=config)

    @app.get("/")
    def endpoint():
        warnings.warn("Repeated endpoint warning", UserWarning, stacklevel=1)
        return "ok"

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        warnings.warn("Repeated endpoint warning", UserWarning, stacklevel=1)
        await websocket.close()

    get_tracer.assert_not_called()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        with TestClient(app) as client:
            for _ in range(5):
                if protocol == "http":
                    assert client.get("/").json() == "ok"
                else:
                    with client.websocket_connect("/ws") as websocket:
                        assert websocket.receive()["type"] == "websocket.close"
    assert [str(warning.message) for warning in caught] == ["Repeated endpoint warning"]
    get_tracer.assert_called_once()
    spans = server_spans(exporter)
    assert len(spans) == 5
    assert len({span.context.trace_id for span in spans}) == 5
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
