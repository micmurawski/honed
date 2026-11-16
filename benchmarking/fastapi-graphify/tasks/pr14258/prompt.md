Show a clear error on attempt to include router into itself.

For example, this test should pass once it's done:

```python
import pytest

from fastapi import APIRouter

def test_router_circular_import():
    router = APIRouter()

    with pytest.raises(
        AssertionError,
        match="Cannot include the same APIRouter instance into itself. Did you mean to include a different router?",
    ):
        router.include_router(router)
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
