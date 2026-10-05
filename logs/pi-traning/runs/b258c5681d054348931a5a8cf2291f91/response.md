Now I have enough context. Let me check the existing test structure to understand the testing conventions:

Let me check how `BaseHTTPMiddleware` is imported and used:

Let me check the `add_middleware` method:

Now I have enough understanding. Let me make the implementation:

Now let me check the imports and the end of the constructor parameter list:

Now I have a clear picture. Let me implement the changes. First, add the `uuid` import:

Now add the `add_request_id_header` parameter to the constructor:

Let me try a smaller, more precise edit:

Now let's add the `RequestIDMiddleware` class. Let me add it just before the `FastAPI` class definition:

Now let's modify the `__init__` body to actually use this parameter. Let me find where the `user_middleware` is set:

Now let me add the logic to inject the middleware in `__init__`:

Now let's verify the changes look correct:

Now let me create the test file:

Now let's run the test:

All 5 tests pass. Let me also run the test as the user requested with the exact command:

All 5 tests pass. Let me also verify the changes I made by looking at the diff:

All tests pass. Here's a summary of what was done:

## Changes

### `fastapi/applications.py`

1. **Added `import uuid`** at the top of the file.

2. **Added `RequestIDMiddleware`** class (a `BaseHTTPMiddleware` subclass) that:
   - Generates a UUID4 for each incoming request
   - Sets it as `request.state.request_id` so handlers/dependencies can read it
   - Adds it to the response as the `X-Request-ID` header

3. **Added `add_request_id_header: bool = False`** parameter to `FastAPI.__init__()`, with a full `Annotated` docstring consistent with the existing parameter style.

4. **Wired the parameter** in the constructor body: when `True`, `Middleware(RequestIDMiddleware)` is inserted at the beginning of `self.user_middleware`, so it is the outermost user middleware (still inside `ServerErrorMiddleware`/`ExceptionMiddleware`). This makes the request ID available to all subsequent user middlewares, path operations, and dependencies.

### `tests/test_request_id_middleware.py` (new)

Five tests covering:
- `X-Request-ID` is present in the response when `add_request_id_header=True`
- The value is a valid UUID4 (`uuid.UUID(...).version == 4`)
- The header is **absent** when the flag is `False` (default behavior unchanged)
- Each request gets a unique ID (no reuse across requests)
- `request.state.request_id` is set and matches the response header

## Test run

```
tests/test_request_id_middleware.py::test_request_id_header_present_when_enabled PASSED
tests/test_request_id_middleware.py::test_request_id_header_is_valid_uuid4 PASSED
tests/test_request_id_middleware.py::test_request_id_header_absent_when_disabled PASSED
tests/test_request_id_middleware.py::test_request_id_unique_per_request PASSED
tests/test_request_id_middleware.py::test_request_id_exposed_on_request_state PASSED
============================== 5 passed in 0.29s ===============================
```