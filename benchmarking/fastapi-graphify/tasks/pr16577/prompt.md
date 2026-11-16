Add support for combining form models.

For example, this test should pass once it's done:

```python
import importlib

import pytest

from fastapi.testclient import TestClient

def test_missing_file(client: TestClient):
    response = client.post("/profiles/", data={"name": "Rick"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "avatar"]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
