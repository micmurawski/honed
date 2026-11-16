Respect model alias settings for query, header, and cookie parameters.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI, Header, Query

from fastapi._compat import PYDANTIC_VERSION_MINOR_TUPLE

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

@pytest.mark.skipif(
    PYDANTIC_VERSION_MINOR_TUPLE < (2, 11),
    reason="validate_by_name and validate_by_alias require Pydantic 2.11",
)
@pytest.mark.parametrize(
    "field_info",
    [
        Field(alias="tag"),
        Field(validation_alias="tag"),
        Field(validation_alias=AliasChoices("item", "tag")),
    ],
)
def test_model_can_disable_validation_by_alias(field_info):
    app = FastAPI()

    class Model(BaseModel):
        model_config = ConfigDict(validate_by_alias=False, extra="forbid")
        items: Annotated[list[str], field_info]

    @app.get("/")
    def read(data: Annotated[Model, Query()]):
        return {"items": data.items}

    client = TestClient(app)
    response = client.get("/", params={"items": "one"})
    assert response.status_code == 200
    assert response.json() == {"items": ["one"]}
    response = client.get("/", params={"items": "one", "tag": "two"})
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "extra_forbidden",
                "loc": ["query", "tag"],
                "msg": "Extra inputs are not permitted",
                "input": "two",
            }
        ]
    }
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
