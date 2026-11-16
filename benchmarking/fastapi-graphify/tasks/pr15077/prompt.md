Fix stream item type lost when using `include_router()`.

For example, this test should pass once it's done:

```python
import json

from typing import AsyncIterable, Iterable

from fastapi import APIRouter, FastAPI

from fastapi.testclient import TestClient

from pydantic import BaseModel

class Item(BaseModel):
    name: str
    optional: str | None = None

app = FastAPI()

@app.get("/items/stream-bare-async")
async def stream_bare_async() -> AsyncIterable:
    yield {"name": "foo"}

@app.get("/items/stream-bare-sync")
def stream_bare_sync() -> Iterable:
    yield {"name": "bar"}

router = APIRouter()

@router.get("/events-jsonl", response_model_exclude_none=True)
async def stream_events_jsonl() -> AsyncIterable[Item]:
    yield Item(name="foo")

app.include_router(router, prefix="/api")

client = TestClient(app)

def test_jsonl_router_typed_stream():
    response = client.get("/api/events-jsonl")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/jsonl"
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert lines == [{"name": "foo"}]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
