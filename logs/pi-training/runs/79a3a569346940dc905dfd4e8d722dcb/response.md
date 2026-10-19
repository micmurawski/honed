Now I have a clear picture. Let me implement the request ID middleware.

Let me verify the final state of all the modified files:

All 6 tests pass.

## Summary

I added built-in automatic request ID tracking to FastAPI. Here's what changed:

### 1. New middleware: `fastapi/middleware/requestid.py`
A pure ASGI `RequestIDMiddleware` class that:
- Generates a fresh `uuid.uuid4()` for each incoming HTTP request.
- Stores it in `scope["state"]["request_id"]`, so handlers see it as `request.state.request_id` (Starlette's `Request.state` property delegates to that dict).
- Wraps the ASGI `send` callable to inject `X-Request-ID` into every outgoing response's headers.
- Passes WebSocket and lifespan scopes through untouched.

### 2. `fastapi/applications.py` changes
- Imported `RequestIDMiddleware` from the new module.
- Added a new keyword-only `add_request_id_header: bool = False` parameter to `FastAPI.__init__`, fully documented with `Annotated[..., Doc(...)]` matching the existing style.
- Stored it on the instance as `self.add_request_id_header`.
- Updated `build_middleware_stack` so that, when `add_request_id_header=True`, `Middleware(RequestIDMiddleware)` is prepended to the middleware list, making it the **outermost** wrapper — so even500 responses synthesized by `ServerErrorMiddleware` carry the header.

### 3. New test file: `tests/test_request_id_middleware.py`
Six tests covering:
- `X-Request-ID` is present in the response headers.
- It parses as a valid UUID4 (version field + RFC 4122 variant bits).
- It matches `request.state.request_id` exposed inside the handler.
- Each request gets a fresh, unique UUID.
- The header is absent by default (when `add_request_id_header` is not set).
- The header is added even on error responses (e.g. 500 from `ServerErrorMiddleware`).

### Test result
```
$ pytest -W ignore tests/test_request_id_middleware.py
============================== 6 passed in 0.31s ==============================
```

I also ran the full project test suite (`tests/`, excluding tutorials/benchmarks): **2045 passed, 9 skipped**, no regressions.