Add `strict_content_type` checking for JSON requests.

For example, this test should pass once it's done:

```python
import importlib

import pytest

from fastapi.testclient import TestClient

def test_lax_post_without_content_type_is_parsed_as_json(client: TestClient):
    response = client.post(
        "/items/",
        content='{"name": "Foo", "price": 50.5}',
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "Foo", "price": 50.5}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
