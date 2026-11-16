Add support for optional query, header, and cookie parameter models.

For example, this test should pass once it's done:

```python
import importlib

import pytest

from fastapi.testclient import TestClient

def test_preferences(client: TestClient):
    response = client.get("/items/", headers={"cookie": "theme=dark; locale=en"})
    assert response.status_code == 200, response.text
    assert response.json() == {"theme": "dark", "locale": "en"}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
