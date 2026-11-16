Validate Server Sent Event fields to avoid applications from sending broken data.

For example, this test should pass once it's done:

```python
import asyncio

import time

from collections.abc import AsyncIterable, Iterable

import fastapi.routing

import pytest

from fastapi import APIRouter, FastAPI

from fastapi.responses import EventSourceResponse

from fastapi.sse import ServerSentEvent

from fastapi.testclient import TestClient

from pydantic import BaseModel

class Item(BaseModel):
    name: str
    description: str | None = None

items = [
    Item(name="Plumbus", description="A multi-purpose household device."),
    Item(name="Portal Gun", description="A portal opening device."),
    Item(name="Meeseeks Box", description="A box that summons a Meeseeks."),
]

app = FastAPI()

@app.get("/items/stream", response_class=EventSourceResponse)
async def sse_items() -> AsyncIterable[Item]:
    for item in items:
        yield item

@app.get("/items/stream-sync", response_class=EventSourceResponse)
def sse_items_sync() -> Iterable[Item]:
    yield from items

@app.get("/items/stream-no-annotation", response_class=EventSourceResponse)
async def sse_items_no_annotation():
    for item in items:
        yield item

@app.get("/items/stream-sync-no-annotation", response_class=EventSourceResponse)
def sse_items_sync_no_annotation():
    yield from items

@app.get("/items/stream-dict", response_class=EventSourceResponse)
async def sse_items_dict():
    for item in items:
        yield {"name": item.name, "description": item.description}

@app.get("/items/stream-sse-event", response_class=EventSourceResponse)
async def sse_items_event():
    yield ServerSentEvent(data="hello", event="greeting", id="1")
    yield ServerSentEvent(data={"key": "value"}, event="json-data", id="2")
    yield ServerSentEvent(comment="just a comment")
    yield ServerSentEvent(data="retry-test", retry=5000)

@app.get("/items/stream-mixed", response_class=EventSourceResponse)
async def sse_items_mixed() -> AsyncIterable[Item]:
    yield items[0]
    yield ServerSentEvent(data="custom-event", event="special")
    yield items[1]

@app.get("/items/stream-string", response_class=EventSourceResponse)
async def sse_items_string():
    yield ServerSentEvent(data="plain text data")

@app.post("/items/stream-post", response_class=EventSourceResponse)
async def sse_items_post() -> AsyncIterable[Item]:
    for item in items:
        yield item

@app.get("/items/stream-raw", response_class=EventSourceResponse)
async def sse_items_raw():
    yield ServerSentEvent(raw_data="plain text without quotes")
    yield ServerSentEvent(raw_data="<div>html fragment</div>", event="html")
    yield ServerSentEvent(raw_data="cpu,87.3,1709145600", event="csv")

router = APIRouter()

@router.get("/events", response_class=EventSourceResponse)
async def stream_events():
    yield {"msg": "hello"}
    yield {"msg": "world"}

# …
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
