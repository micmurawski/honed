Fix `AliasChoices` extraction for form models.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI, File, Form, UploadFile, params

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

def test_model_files_can_share_an_unused_alias_choice():
    app = FastAPI()

    class FileModel(BaseModel):
        files: Annotated[
            list[UploadFile], Field(validation_alias=AliasChoices("files", "legacy"))
        ]
        file: Annotated[
            UploadFile, Field(validation_alias=AliasChoices("file", "legacy"))
        ]

    @app.post("/")
    def read_form(data: Annotated[FileModel, Form()]):
        return {
            "files": [file.filename for file in data.files],
            "file": data.file.filename,
        }

    client = TestClient(app)
    response = client.post(
        "/",
        files=[
            ("legacy", ("one.txt", b"one")),
            ("legacy", ("two.txt", b"two")),
            ("file", ("hello.txt", b"hello")),
        ],
    )
    assert response.status_code == 200
    assert response.json() == {"files": ["one.txt", "two.txt"], "file": "hello.txt"}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
