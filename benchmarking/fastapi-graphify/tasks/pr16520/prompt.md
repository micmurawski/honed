Deprecate the `root_path` parameter in `FastAPI`.

For example, this test should pass once it's done:

```python
import pytest

from fastapi.exceptions import FastAPIDeprecationWarning

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

def test_main(client: TestClient):
    response = client.get("/app")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello World", "root_path": "/api/v1"}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
