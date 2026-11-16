Fix non-finite float serialization in JSONL and SSE streams.

For example, this test should pass once it's done:

```python
import json

from collections.abc import AsyncIterator

import pytest

from fastapi import FastAPI

from fastapi.sse import EventSourceResponse, ServerSentEvent

from fastapi.testclient import TestClient

from pydantic import BaseModel, ConfigDict

@pytest.mark.parametrize("path", ["/jsonl", "/sse", "/sse-events"])
def test_untyped_stream_non_finite_floats(path: str):
    app = FastAPI()
    values = [float("nan"), float("inf"), float("-inf")]
    items = [
        *values,
        {"nested": [values, {"value": values[0]}]},
        *(Item(value=value) for value in values),
        {
            "finite": 1.5,
            "integer": 1,
            "boolean": True,
            "null": None,
            "text": "NaN Infinity -Infinity",
            "keys": {None: "null key", float("inf"): "infinite key"},
        },
    ]

    @app.get("/jsonl")
    @app.get("/sse", response_class=EventSourceResponse)
    async def stream():
        for item in items:
            yield item

    @app.get("/sse-events", response_class=EventSourceResponse)
    async def stream_events():
        for item in items:
            yield ServerSentEvent(data=item)

    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 200
    lines = [line.removeprefix("data: ") for line in response.text.splitlines() if line]
    assert [json.loads(line) for line in lines] == [
        None,
        None,
        None,
        {"nested": [[None, None, None], {"value": None}]},
        {"value": None},
        {"value": None},
        {"value": None},
        {
            "finite": 1.5,
            "integer": 1,
            "boolean": True,
            "null": None,
            "text": "NaN Infinity -Infinity",
            "keys": {"null": "null key", "Infinity": "infinite key"},
        },
    ]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
