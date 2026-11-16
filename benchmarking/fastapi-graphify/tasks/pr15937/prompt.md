Fix `status_code` being ignored for SSE and JSONL streaming endpoints.

For example, this test should pass once it's done:

```python
from collections.abc import AsyncIterable

import pytest

from fastapi import Depends, FastAPI, Response

from fastapi.responses import EventSourceResponse, StreamingResponse

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

SSE_RESPONSE = {
    "description": "Successful Response",
    "content": {
        "text/event-stream": {
            "itemSchema": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "title": "SSE stream item",
                        },
                    },
                    "event": {"type": "string"},
                    "id": {"type": "string"},
                    "retry": {"type": "integer", "minimum": 0},
                },
                "required": ["data"],
            }
        }
    },
}

JSONL_RESPONSE = {
    "description": "Successful Response",
    "content": {
        "application/jsonl": {
            "itemSchema": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "title": "JSONL stream item",
            }
        }
    },
}

app = FastAPI()

def set_accepted(response: Response) -> None:
    response.status_code = 202

@app.post("/sse", response_class=EventSourceResponse, status_code=201)
async def sse() -> AsyncIterable[dict[str, str]]:
    yield {"message": "created"}

@app.post("/jsonl", status_code=201)
async def jsonl() -> AsyncIterable[dict[str, str]]:
    yield {"message": "created"}

@app.post("/raw", response_class=StreamingResponse, status_code=201)
async def raw() -> AsyncIterable[str]:
    yield "accepted"

@app.post(
    "/sse-dependency",
    response_class=EventSourceResponse,
    responses={202: SSE_RESPONSE},
)
async def sse_dependency(
    accepted: None = Depends(set_accepted),
) -> AsyncIterable[dict[str, str]]:
    yield {"message": "accepted"}

@app.post("/jsonl-dependency", responses={202: JSONL_RESPONSE})
async def jsonl_dependency(
    accepted: None = Depends(set_accepted),
) -> AsyncIterable[dict[str, str]]:
    yield {"message": "accepted"}

@app.post(
    "/raw-dependency",
    response_class=StreamingResponse,
    responses={202: {"description": "Accepted"}},
)
async def raw_dependency(
# …
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
