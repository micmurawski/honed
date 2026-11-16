Fix handling of empty value for `byte` file list parameter.

For example, this test should pass once it's done:

```python
from fastapi import FastAPI, File

from fastapi.testclient import TestClient

app = FastAPI()

@app.post("/files")
async def upload_files(files: list[bytes] | None = File(None)):
    if files is None:
        return {"files_count": 0}
    return {"files_count": len(files), "sizes": [len(f) for f in files]}

def test_optional_bytes_list_send_empty_str():
    client = TestClient(app)
    response = client.post(
        "/files",
        data={"files": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"files_count": 1, "sizes": [0]}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
