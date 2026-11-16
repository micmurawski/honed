Make the result of `Depends()` and `Security()` hashable, as a workaround for other tools interacting with these internal parts.

For example, this test should pass once it's done:

```python
from fastapi import Depends, Security

def dep():
    pass

def test_depends_hashable():
    dep()  # just for coverage
    d1 = Depends(dep)
    d2 = Depends(dep)
    d3 = Depends(dep, scope="function")
    d4 = Depends(dep, scope="function")

    s1 = Security(dep)
    s2 = Security(dep)

    assert hash(d1) == hash(d2)
    assert hash(s1) == hash(s2)
    assert hash(d1) != hash(d3)
    assert hash(d3) == hash(d4)
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
