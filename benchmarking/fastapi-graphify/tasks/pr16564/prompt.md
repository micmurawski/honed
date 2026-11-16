Fix validation error locations for form and file `AliasChoices`.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI, File, Form, UploadFile, params

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

def test_unselected_file_alias_choices_use_the_next_alias():
    app = FastAPI()

    @app.post("/")
    def read_file(
        file: Annotated[
            bytes,
            File(alias="legacy", validation_alias=AliasChoices("file", "selected")),
        ],
    ):
        return file.decode()

    client = TestClient(app)
    response = client.post(
        "/",
        content=(
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename=""\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"\r\n--boundary\r\n"
            b'Content-Disposition: form-data; name="selected"; filename="hello.txt"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"hello\r\n--boundary--\r\n"
        ),
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 200
    assert response.json() == "hello"

    response = client.post(
        "/",
        content=EMPTY_FILE_FORM,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 422
    assert [(error["type"], error["loc"]) for error in response.json()["detail"]] == [
        ("missing", ["body", "file"]),
    ]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
