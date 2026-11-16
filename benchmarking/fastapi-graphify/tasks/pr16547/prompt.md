Preserve Pydantic `Field()` attributes in request parameters.

For example, this test should pass once it's done:

```python
from typing import Annotated

from unittest.mock import Mock

import pytest

from fastapi import Body, Cookie, FastAPI, File, Form, Header, Query

from fastapi.testclient import TestClient

from pydantic import BaseModel, BeforeValidator, Field

@pytest.mark.parametrize(
    "field,expected", [(Query(), 20), (Query(validate_default=False), 10)]
)
def test_parameter_preserves_pydantic_validate_default(field, expected):
    app = FastAPI()

    @app.get("/")
    def read_parameter(
        value: Annotated[ValidatedInt, Field(validate_default=True), field] = 10,
    ):
        return value

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == expected
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
