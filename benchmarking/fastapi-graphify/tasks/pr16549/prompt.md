Preserve Pydantic field metadata when request options are omitted.

For example, this test should pass once it's done:

```python
from typing import Annotated, Literal

import pytest

from dirty_equals import IsOneOf

from fastapi import Body, Cookie, FastAPI, File, Form, Header, Path, Query

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import BaseModel, Field

def test_file_field_alias_is_preserved():
    app = FastAPI()

    @app.post("/")
    def read_file(value: Annotated[bytes, Field(alias="upload"), File()]):
        return value

    client = TestClient(app)
    response = client.post("/", files={"upload": ("example.txt", b"hello")})
    assert response.status_code == 200
    assert response.json() == "hello"
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
