Update the lru_cache limit for dependencies to account for large apps.

For example, this test should pass once it's done:

```python
from collections.abc import AsyncGenerator, Callable, Generator

from typing import Any

from fastapi.dependencies.models import (
    Dependant,
    _get_cache_key,
    _get_computed_scope,
    _get_oauth_scopes,
    _get_security_dependencies,
    _get_security_scheme,
    _is_async_gen_callable,
    _is_async_gen_callable_cached,
    _is_coroutine_callable,
    _is_coroutine_callable_cached,
    _is_gen_callable,
    _is_gen_callable_cached,
    _is_security_scheme,
    _uses_scopes,
)

from fastapi.security import APIKeyHeader

def sync_dependency() -> None:
    pass  # pragma: no cover

async def async_dependency() -> None:
    pass  # pragma: no cover

def generator_dependency() -> Generator[None, None, None]:
    yield  # pragma: no cover

async def async_generator_dependency() -> AsyncGenerator[None, None]:
    yield  # pragma: no cover

class UnhashableCallable:
    __hash__ = None

    async def __call__(self) -> None:
        pass  # pragma: no cover

class UnhashableGeneratorCallable:
    __hash__ = None

    def __call__(self) -> Generator[None, None, None]:
        yield  # pragma: no cover

class UnhashableAsyncGeneratorCallable:
    __hash__ = None

    async def __call__(self) -> AsyncGenerator[None, None]:
        yield  # pragma: no cover

class EqualCallable:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, EqualCallable)

    def __hash__(self) -> int:
        return 1  # pragma: no cover

class EqualAsyncCallable(EqualCallable):
    async def __call__(self) -> None:
        pass  # pragma: no cover

class EqualSyncCallable(EqualCallable):
    def __call__(self) -> None:
        pass  # pragma: no cover

def test_callable_classification_cache_supports_large_apps() -> None:
    callables: list[Callable[[], None]] = [lambda: None for _ in range(3000)]

    for classifier, cached_classifier in (
        (_is_gen_callable, _is_gen_callable_cached),
        (_is_async_gen_callable, _is_async_gen_callable_cached),
        (_is_coroutine_callable, _is_coroutine_callable_cached),
    ):
        cached_classifier.cache_clear()

        for _ in range(2):
            assert all(not classifier(call) for call in callables)

        cache_info = cached_classifier.cache_info()
        assert cache_info.hits == len(callables)
        assert cache_info.misses == len(callables)
        assert cache_info.maxsize == 4096
        cached_classifier.cache_clear()
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
