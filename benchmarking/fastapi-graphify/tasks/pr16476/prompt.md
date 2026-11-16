Require opt-in for automatic OpenTelemetry exporter setup.

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

@pytest.mark.parametrize(
    "env,config",
    [
        ({}, {}),
        ({"FASTAPI_OTEL_AUTO_CONFIGURE": "false"}, {}),
        ({"FASTAPI_OTEL_AUTO_CONFIGURE": "true"}, {"auto_configure": False}),
    ],
)
@run_in_subprocess
def test_external_globals_are_unchanged_when_auto_configuration_is_disabled(
    env, config
):
    import os

    os.environ.update({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})
    os.environ.update(env)

    from fastapi import FastAPI
    from fastapi.telemetry import _runtime
    from fastapi.testclient import TestClient
    from opentelemetry import _logs, metrics, trace
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    mp = MeterProvider()
    lp = LoggerProvider()
    _logs.set_logger_provider(lp)
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(mp)
    for _ in range(2):
        with TestClient(FastAPI(telemetry=config)) as client:
            client.get("/")
    assert trace.get_tracer_provider() is tp
    assert metrics.get_meter_provider() is mp
    assert _logs.get_logger_provider() is lp
    assert not _runtime._owned
    assert len(exporter.get_finished_spans()) == 2
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
