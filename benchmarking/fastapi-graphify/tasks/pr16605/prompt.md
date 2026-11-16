Restore default handling for omitted boolean form fields.

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

@pytest.mark.parametrize("path", ["/parameter", "/model"])
@pytest.mark.parametrize("annotation", [bool, bool | None])
def test_omitted_required_boolean_is_missing(path, annotation):
    app = FastAPI()

    class Model(BaseModel):
        value: annotation

    @app.post("/parameter")
    def read_parameter(value: Annotated[annotation, Form()]):
        return {"value": value}

    @app.post("/model")
    def read_model(params: Annotated[Model, Form()]):
        return params.model_dump(exclude_unset=True)

    client = TestClient(app)
    response = client.post(path, data={"value": "on"})
    assert response.status_code == 200
    assert response.json() == {"value": True}
    response = client.post(path)
    assert response.status_code == 422
    assert [(error["type"], error["loc"]) for error in response.json()["detail"]] == [
        ("missing", ["body", "value"]),
    ]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
