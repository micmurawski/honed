Fix `AliasChoices` for path parameters.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import APIRouter, Depends, FastAPI, Path, WebSocket

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, AliasPath

@pytest.mark.parametrize("alias", ["first", "second"])
def test_individual_path_alias_choices(alias: str):
    app = FastAPI()

    @app.get("/{" + alias + "}")
    def read(
        value: Annotated[int, Path(validation_alias=AliasChoices("first", "second"))],
    ):
        return value

    client = TestClient(app)
    response = client.get("/1")
    assert response.status_code == 200
    assert response.json() == 1
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
