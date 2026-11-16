Fix handling sequences with nested Annotated types.

For example, this test should pass once it's done:

```python
from typing import Annotated

from dirty_equals import IsList

from fastapi import FastAPI, Query

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import Field

MaxSizedSet = Annotated[set[str], Field(max_length=3)]

app = FastAPI()

@app.get("/")
def read_root(foo: Annotated[MaxSizedSet | None, Query()] = None):
    return {"foo": foo}

client = TestClient(app)

def test_endpoint_none():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"foo": None}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
