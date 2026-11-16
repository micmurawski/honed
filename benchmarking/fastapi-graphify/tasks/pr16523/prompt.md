Treat omitted boolean form fields as False.

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

def test_unchecked_checkbox_with_alias_choices_overrides_defaults():
    app = FastAPI()

    class Model(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: Annotated[
            bool, Field(validation_alias=AliasChoices("enabled", "checkbox"))
        ] = True
        nullable: Annotated[
            bool | None, Field(validation_alias=AliasChoices("optional", "nullable"))
        ] = None

    @app.post("/")
    def read_model(params: Annotated[Model, Form()]):
        return params.model_dump(exclude_unset=True)

    client = TestClient(app)
    response = client.post("/")
    assert response.status_code == 200
    assert response.json() == {"value": False, "nullable": False}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
