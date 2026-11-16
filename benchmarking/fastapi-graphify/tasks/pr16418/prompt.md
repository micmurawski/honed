Allow startup when automatic OpenTelemetry configuration fails.

For example, this test should pass once it's done:

```python
import asyncio

import logging

import threading

from contextlib import asynccontextmanager

from unittest.mock import Mock

import pytest

from fastapi import FastAPI

from fastapi.exceptions import FastAPIError

from fastapi.telemetry import _runtime as runtime

from fastapi.testclient import TestClient

from ._otlp import otlp_collector

from ._subprocess import run_in_subprocess

@pytest.mark.parametrize("stage", ["startup", "shutdown"])
def test_application_lifespan_errors_still_propagate(monkeypatch, stage):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "not-a-url")

    @asynccontextmanager
    async def lifespan(app):
        if stage == "startup":
            raise RuntimeError("application startup failed")
        yield
        raise RuntimeError("application shutdown failed")

    with pytest.raises(RuntimeError, match=f"application {stage} failed"):
        with TestClient(FastAPI(lifespan=lifespan)) as client:
            assert client.get("/").status_code == 404
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
