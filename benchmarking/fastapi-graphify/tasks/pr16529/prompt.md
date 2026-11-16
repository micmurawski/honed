Treat empty scalar values as missing in form models.

For example, this test should pass once it's done:

```python
from typing import Annotated

from unittest.mock import Mock

import pytest

from fastapi import Body, FastAPI, Form

from fastapi._compat import PYDANTIC_VERSION_MINOR_TUPLE

from fastapi.testclient import TestClient

from pydantic import (
    AfterValidator,
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

@pytest.mark.parametrize("validate_default,expected", [(False, 10), (True, 20)])
def test_empty_form_model_value_respects_default_validation(validate_default, expected):
    app = FastAPI()

    class Model(BaseModel):
        value: Annotated[
            int,
            AfterValidator(lambda value: value * 2),
            Field(validate_default=validate_default),
        ] = 10

    @app.post("/")
    def read_model(params: Annotated[Model, Form()]):
        return {
            "values": params.model_dump(),
            "supplied": params.model_dump(exclude_unset=True),
        }

    client = TestClient(app)
    response = client.post("/", data={"value": ""})
    assert response.status_code == 200
    assert response.json() == {"values": {"value": expected}, "supplied": {}}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
