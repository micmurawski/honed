Let me first check the graphify knowledge graph for relevant information about the FastAPI class and middleware.

Now let me directly inspect the FastAPI source code to understand the structure.

Let me look at the existing middleware files to understand the patterns used in FastAPI.

Let me check existing tests to understand the test patterns and the conftest.

Let me look at the Starlette `add_middleware` flow to understand how middleware wraps the app.

Let me look at how UUID is used in any existing test and also check pyproject for any existing patterns.

Now let me check how Starlette's BaseHTTPMiddleware works to understand the proper approach for ASGI middleware that needs to set response headers.

Let me also check the build_middleware_stack flow and look at the existing exception handlers to see if there's a pattern I should follow.

Let me check how Starlette's `Request.state` works.

Now I have enough context. Let me also verify the imports needed and check how the existing `setup()` is called.

Let me also check that `MutableHeaders` is the right import to use, and look at how `BaseHTTPMiddleware` is used.

Now I have enough context to start implementing. Let me ask the user a quick clarifying question about scope before making the changes.

I'll proceed with the recommended defaults: add the parameter before `**extra` and create a new `fastapi/middleware/request_id.py` file matching the `asyncexitstack.py` pattern.

## Step 1: Create the RequestIDMiddleware

## Step 2: Add the parameter and wire it into `FastAPI.__init__`

Now let me update `applications.py`. First, I'll add the import:

Now let me add the `add_request_id_header` parameter before `**extra`:

Now let me wire up the middleware in the `__init__` body. I'll add the registration right after the existing `self.user_middleware` initialization:

Let me verify the changes look correct:

Let me also verify the parameter is correctly added by reading the area around it:

## Step 3: Write the test file

## Step 4: Run the tests

All 7 tests pass. Let me also run the broader test suite to make sure I haven't broken anything else:

Existing tests still pass. Let me also run a quick sanity check that FastAPI still imports cleanly:

Let me run a wider set of tests to make sure nothing else regressed:

All related tests pass. Now let me run the test exactly as the user requested:

All 7 tests pass. Let me give a final summary of what was implemented.

## Summary

I added a built-in mechanism for automatic request ID tracking in the FastAPI source code, following the existing patterns in the codebase.

### Files changed

1. **`fastapi/middleware/request_id.py`** (new) — Defines `RequestIDMiddleware`, an ASGI middleware that mirrors the pattern of `fastapi/middleware/asyncexitstack.py`. For each incoming HTTP request it:
   - Generates a fresh `uuid.uuid4()`.
   - Stashes it on the request scope via `request.state.request_id` so any `Request` instance built from that scope (including the one FastAPI's router constructs) can read it.
   - Wraps `send` to inject `X-Request-ID` into the response headers.
   - Passes through non-HTTP scopes (WebSocket, lifespan) untouched.

2. **`fastapi/applications.py`** — Two edits:
   - Imported `RequestIDMiddleware` next to the other middleware import.
   - Added a new `add_request_id_header: bool = False` keyword-only parameter to `FastAPI.__init__`, documented with `Annotated[..., Doc(...)]` to match the style of every other parameter, placed immediately before `**extra`.
   - When the flag is `True`, `self.user_middleware.insert(0, Middleware(RequestIDMiddleware))` is called in `__init__` so the middleware becomes the outermost user middleware. That means it wraps the rest of `user_middleware` and `ExceptionMiddleware`, so the `X-Request-ID` header is set on every response — including validation errors and 404s — and `request.state.request_id` is visible to every downstream middleware and route handler. Default is unchanged when the flag is not passed.

3. **`tests/test_request_id_middleware.py`** (new) — 7 tests covering the three required assertions plus regression coverage:
   - `test_request_id_header_present` — header exists.
   - `test_request_id_header_is_valid_uuid4` — value parses as a UUID and `version == 4`.
   - `test_request_id_header_format` — 36 chars, lowercase canonical form.
   - `test_request_id_unique_per_request` — two requests get different IDs.
   - `test_request_id_exposed_in_request_state` — `request.state.request_id` matches the response header.
   - `test_request_id_on_post_request` — works for non-GET methods.
   - `test_request_id_header_absent_by_default` — flag-off apps are unchanged.

### Test result

```
$ pytest -W ignore tests/test_request_id_middleware.py
collected 7 items
tests/test_request_id_middleware.py .......                              [100%]
============================== 7 passed in 0.28s ===============================
```

All 7 tests pass, and the surrounding test files (`test_application.py`, `test_custom_middleware_exception.py`, `test_exception_handlers.py`, `test_starlette_exception.py`) also still pass, confirming no regression.