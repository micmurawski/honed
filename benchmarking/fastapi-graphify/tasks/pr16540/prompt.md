Apply browser form handling to unions of Pydantic models.

For example, this test should pass once it's done:

```python
from collections.abc import Mapping

from typing import Annotated, Any, Literal

import pytest

from dirty_equals import IsOneOf

from fastapi import FastAPI, Form, UploadFile

from fastapi._compat import PYDANTIC_VERSION_MINOR_TUPLE

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    model_validator,
)

def test_model_validator_does_not_mutate_other_branches():
    app = FastAPI()

    class FirstForm(BaseModel):
        value: str

        @model_validator(mode="before")
        @classmethod
        def remove_value(cls, data):
            data.pop("value", None)
            return data

    class SecondForm(BaseModel):
        value: list[str]

    @app.post("/")
    def read_form(data: Annotated[FirstForm | SecondForm, Form()]):
        return data

    client = TestClient(app)
    response = client.post("/", data={"value": ["", "hello"]})
    assert response.status_code == 200
    assert response.json() == {"value": ["", "hello"]}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
