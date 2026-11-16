Serialize JSON response with Pydantic (in Rust), when there's a Pydantic return type or response model.

For example, this test should pass once it's done:

```python
from unittest.mock import patch

from fastapi import FastAPI

from fastapi.responses import JSONResponse

from fastapi.testclient import TestClient

from pydantic import BaseModel

class Item(BaseModel):
    name: str
    price: float

app = FastAPI()

@app.get("/default")
def get_default() -> Item:
    return Item(name="widget", price=9.99)

@app.get("/explicit", response_class=JSONResponse)
def get_explicit() -> Item:
    return Item(name="widget", price=9.99)

client = TestClient(app)

def test_default_response_class_skips_json_dumps():
    """When no response_class is set, the fast path serializes directly to
    JSON bytes via Pydantic's dump_json and never calls json.dumps."""
    with patch(
        "starlette.responses.json.dumps", wraps=__import__("json").dumps
    ) as mock_dumps:
        response = client.get("/default")
    assert response.status_code == 200
    assert response.json() == {"name": "widget", "price": 9.99}
    mock_dumps.assert_not_called()
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
