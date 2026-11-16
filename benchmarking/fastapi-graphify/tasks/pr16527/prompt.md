Fix default handling for request parameters and models.

For example, this test should pass once it's done:

```python
import importlib

import pytest

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from tests.utils import needs_py310

def test_header_param_model_invalid(client: TestClient):
    response = client.get("/items/")
    assert response.status_code == 422
    assert response.json() == snapshot(
        {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["header", "save_data"],
                    "msg": "Field required",
                    "input": {"host": "testserver"},
                }
            ]
        }
    )
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
