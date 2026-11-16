Fix OpenAPI schemas for standalone nullable form unions.

For example, this test should pass once it's done:

```python
from typing import Annotated, Literal, Optional

import pytest

from fastapi import Depends, FastAPI, Form

from fastapi.testclient import TestClient

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Tag,
    model_validator,
)

def test_nullable_union_without_default_is_required():
    app = FastAPI()

    class Count(BaseModel):
        count: int = 1

    class Label(BaseModel):
        label: str = "default"

    @app.post("/")
    def read(data: Annotated[Count | Label | None, Form()]):
        return data

    client = TestClient(app)
    for fields in ({}, {"unknown": "ignored"}):
        response = client.post("/", data=fields)
        assert response.status_code == 422
        assert response.json()["detail"] == [
            {"type": "missing", "loc": ["body"], "msg": "Field required", "input": None}
        ]
    response = client.post("/", data={"count": "2"})
    assert response.status_code == 200, response.text
    assert response.json() == {"count": 2}
    schema = app.openapi()
    assert schema["paths"]["/"]["post"]["requestBody"]["required"] is True
    assert schema["components"]["schemas"]["Body_read__post"]["allOf"] == [
        {"not": {"not": {"anyOf": [{"required": ["count"]}, {"required": ["label"]}]}}},
        {
            "anyOf": [
                {"$ref": "#/components/schemas/Count"},
                {"$ref": "#/components/schemas/Label"},
            ],
            "title": "Data",
        },
    ]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
