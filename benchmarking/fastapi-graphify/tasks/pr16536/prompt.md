Distinguish JSON null from omitted body values.

For example, this test should pass once it's done:

```python
from typing import Annotated

from unittest.mock import Mock

import pytest

from fastapi import Body, Cookie, FastAPI, Form, Header, Query

from fastapi.testclient import TestClient

from pydantic import BaseModel, BeforeValidator, Field

@pytest.mark.parametrize("default", [None, 10])
def test_explicit_null_body_is_validated(default):
    app = FastAPI()

    @app.post("/")
    def read_body(value: Annotated[ValidatedInt, Body()] = default):
        return value

    client = TestClient(app)
    response = client.post(
        "/", content="null", headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == 0
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
