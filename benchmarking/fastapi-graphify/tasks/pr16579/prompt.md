Preserve form media types in OpenAPI.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import Body, Depends, FastAPI, File, Form

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import BaseModel

@pytest.mark.parametrize("in_dependency", [False, True])
def test_form_model_media_type(in_dependency):
    app = FastAPI()

    class Profile(BaseModel):
        name: str

    class Preferences(BaseModel):
        limit: int

    def preferences_dependency(
        preferences: Annotated[Preferences, Form(media_type="multipart/form-data")],
    ):
        return preferences

    marker = (
        Depends(preferences_dependency)
        if in_dependency
        else Form(media_type="multipart/form-data")
    )

    @app.post("/")
    def submit(
        profile: Annotated[Profile, Form()],
        preferences: Annotated[Preferences, marker],
        token: Annotated[str, Form()],
    ):
        return {"profile": profile, "preferences": preferences, "token": token}

    response = TestClient(app).post(
        "/",
        files={"name": (None, "Rick"), "limit": (None, "2"), "token": (None, "secret")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "profile": {"name": "Rick"},
        "preferences": {"limit": 2},
        "token": "secret",
    }
    schema = app.openapi()
    content = schema["paths"]["/"]["post"]["requestBody"]["content"]
    assert list(content) == ["multipart/form-data"]
    ref = content["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[-1]
    assert set(schema["components"]["schemas"][ref]["properties"]) == {
        "name",
        "limit",
        "token",
    }
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
