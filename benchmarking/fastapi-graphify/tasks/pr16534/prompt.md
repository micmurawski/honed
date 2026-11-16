Treat unselected browser file inputs as missing.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI, File, Form, UploadFile

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import BaseModel, Field

def test_unselected_files_in_form_model_use_defaults_and_remain_unset():
    app = FastAPI()

    class FileModel(BaseModel):
        file: UploadFile | None = None
        files: Annotated[list[UploadFile], Field(default_factory=list)]

    @app.post("/")
    def upload(params: Annotated[FileModel, Form()]):
        assert params.file is None
        assert params.files == []
        return params.model_dump(exclude_unset=True)

    client = TestClient(app)
    response = client.post(
        "/",
        content=EMPTY_FILE_FORM,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 200
    assert response.json() == {}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
