Fix `AliasChoices` for query parameters, headers, and cookies.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI, Header, Query

from fastapi._compat import PYDANTIC_VERSION_MINOR_TUPLE

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field

def test_empty_string_alias():
    app = FastAPI()

    @app.get("/")
    def read(value: Annotated[int, Query(validation_alias=AliasChoices("", "other"))]):
        return value

    client = TestClient(app)
    response = client.get("/?=1&other=2")
    assert response.status_code == 200
    assert response.json() == 1
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
