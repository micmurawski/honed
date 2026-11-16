Fix `AliasChoices` for named JSON body parameters.

For example, this test should pass once it's done:

```python
from typing import Annotated, Any

import pytest

from fastapi import Body, FastAPI

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, AliasPath, BaseModel

def test_model_body_alias_choice_preserves_nested_errors():
    app = FastAPI()

    class Item(BaseModel):
        count: int

    @app.post("/")
    def read(
        item: Annotated[
            Item, Body(embed=True, validation_alias=AliasChoices("item", "data"))
        ],
    ):
        return item

    client = TestClient(app)
    response = client.post("/", json={"data": {"count": 1}})
    assert response.status_code == 200
    assert response.json() == {"count": 1}
    response = client.post("/", json={"data": {"count": "invalid"}})
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "int_parsing",
                "loc": ["body", "data", "count"],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "input": "invalid",
            }
        ]
    }
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
