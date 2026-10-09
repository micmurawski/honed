Let me explore the structure of `routing.py` to find `APIRoute.__init__` and `get_route_handler`.

Now let me look at `get_request_handler` to understand response construction flow:

Now let me find `_populate_api_route_state` and `_build_response_args`:

Now let me look at how `add_api_route` works to understand how to propagate the parameter:

Now let me look at the `get`/`post` decorator helpers:

Let me look at the FastAPI application:

Now let me look at one of the decorator helpers like `get` in `applications.py` to understand the structure:

Let me look at how `solved_result.response.headers` is set:

Let me understand `Headers.raw` behavior:

Now I understand the approach. Let me look at the place where the handler can directly return a `Response` object (line 720):

Let me look at an existing test file to understand patterns:

Now let me look at the APIRoute state attributes area and the get_route_handler in detail:

Now I have enough context. Let me also check the `applications.py` `get` method end:

Let me also look at the end of one of the routing.py decorators:

Now let me check the relevant code in `applications.py` at the end of `post`:

Now I have a complete understanding. Let me start implementing.

## Step 1: Modify `fastapi/routing.py`

First, add the `default_response_headers` field annotation to `APIRoute`:

Now update `_populate_api_route_state` to accept and store `default_response_headers`:

Now add the helper function `_apply_default_response_headers` near `_build_response_args`. Let me find a good spot:

Now I need to update all the places in `get_request_handler` where responses are constructed to apply the default headers. Let me look at the relevant areas:

Now let me update all 4 places where `response.headers.raw.extend(solved_result.response.headers.raw)` is called to also apply default headers. Let me find them:

Now I'll update all 4 places. Each is identical so I need to disambiguate them with surrounding context:

Now I need to handle the case where the handler returns a raw `Response` object directly. Let me look at that case:

Now let me check the 5th occurrence at line 2228:

That's a `_FrontendRoute`, not `APIRoute`, so I can skip it. Now update `APIRoute.get_route_handler` to pass the parameter:

Now let me check whether there's a second `APIRoute` constructor call in routing.py (around line 1434):

I need to add the `default_response_headers` field to `_EffectiveRouteContext` and propagate it:

Now let me update `APIRouter.add_api_route` to accept and propagate the parameter:

Now update `APIRouter.api_route`:

Now let me update each of the decorator helpers in `routing.py`. Let me look at the end of `get` in `APIRouter`:

Now let me update the `get` decorator in `routing.py`:

Now let me look at the next decorator `put`:

Let me look at each decorator helper in routing.py and find their unique patterns. Let me look at the structure after each `generate_unique_id_function` parameter docstring:

Let me update `put`:

Now let me look at POST:

Now DELETE:

Now OPTIONS:

Now HEAD:

Now PATCH:

Now TRACE:

Let me verify all `APIRouter` decorators have been updated:

All 8 decorators in `routing.py` are updated. Now let me update `fastapi/applications.py`.

## Step 2: Modify `fastapi/applications.py`

Now update `FastAPI.api_route`:

Now update each `FastAPI` decorator helper. Let me look at the end of the `get` decorator in `applications.py`:

Now let me look at the remaining decorators in `applications.py`. Let me get the end of each:

Let me update PUT:

Now DELETE:

Now OPTIONS:

Now HEAD:

Now PATCH:

Now TRACE:

Now I need to add the `default_response_headers` parameter to the actual function signatures of the `FastAPI` decorator helpers (the parameter list, not just the call). Let me check the parameter list of each one:

Since the patterns are identical for all 8 decorators, let me use a script to do this:

Let me verify by importing the module:

Now let me check that `_apply_default_response_headers` is correctly exported. Let me check how `get_request_handler` is being called and ensure default headers are being applied:

The first test case works. Now let me test the second case (handler value wins):

Let me debug the issue:

Found the bug! `response.headers.raw` contains bytes, not strings. Let me fix the helper:

Now let me re-test:

Now let me also check that the case-insensitive matching works:

Now let me write the test file:

## Step 3: Write tests

Now let me run the tests:

All 6 tests pass. Now let me make sure the existing test suite still passes:

That's an unrelated missing module. Let me skip tutorials:

Let me check the signature consistency test:

I need to add the `Annotated[dict[str, str] | None, Doc(...)]` annotation to the `FastAPI` decorator helpers too. Let me check the FastAPI get parameter list:

Let me look at how my parameter is currently set in `FastAPI.get`:

Let me check what's imported in applications.py:

Now let me look at the test_fastapi_cli failure:

The `test_fastapi_cli` failure is unrelated (missing `coverage` module). Let me fix the `test_signatures_consistency` issue by using the `Annotated` form:

I need to update the `routing.py` decorators to use the same `Annotated` form:

Let me check if `Doc` and `Annotated` are imported in `routing.py`:

Good, both `Doc` and `Annotated` are imported. Let me verify:

Let me run the signature test:

Let me also run my tests:

Now let me run the full test suite (excluding the pre-existing failures):

All 2043 tests pass. Let me confirm the test_fastapi_cli failure is pre-existing by checking git:

Confirmed `test_fastapi_cli` failure is pre-existing (missing `coverage` module). Let me run the exact command the user requested:

All 6 tests pass. Let me also verify by looking at the diff:

## Summary

I implemented per-route default response headers for FastAPI. Here's what changed:

### `fastapi/routing.py`
1. **New helper `_apply_default_response_headers(response, default_response_headers)`**: Injects default headers into a response, but only for keys not already present (case-insensitive). This ensures handler-set headers take precedence.
2. **`APIRoute.__init__`**: New optional kwarg `default_response_headers: dict[str, str] | None = None`.
3. **`_populate_api_route_state`**: Accepts and stores `default_response_headers`.
4. **`get_request_handler`**: Accepts `default_response_headers` and calls the helper in all 5 response-construction paths (SSE, JSONL stream, raw stream, handler returns `Response`, handler returns data).
5. **`APIRoute.get_route_handler`**: Forwards `route.default_response_headers` to `get_request_handler`.
6. **`APIRouter.add_api_route`**, **`APIRouter.api_route`**, and **all 8 decorator helpers** (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`): Accept and propagate the parameter.
7. **`_EffectiveRouteContext`**: Carries the field through `from_api_route` so included routers also propagate it.

### `fastapi/applications.py`
- `FastAPI.add_api_route`, `FastAPI.api_route`, and **all 8 decorator helpers**: Accept and propagate the parameter.

### `tests/test_route_default_headers.py` (new)
6 tests, all passing:
- `test_default_response_headers_applied_to_endpoint` — registers `GET /cached-item` with `{"Cache-Control": "max-age=300", "X-Source": "db"}` and verifies both appear in the response.
- `test_handler_header_overrides_default` — registers a route whose handler sets `Cache-Control: no-store` and confirms the handler's value wins.
- `test_default_response_headers_stored_on_route` — verifies the value is stored on the `APIRoute` instance.
- `test_default_response_headers_none_by_default` — confirms the default is `None` and no extra headers are added.
- `test_default_response_headers_case_insensitive_override` — verifies the case-insensitive merge (no duplicate header).
- `test_default_response_headers_on_post_route` — confirms the feature works on POST routes too.

### Test results
- `pytest -W ignore tests/test_route_default_headers.py` — **6 passed**.
- Full suite (excluding the pre-existing `test_fastapi_cli` failure caused by a missing `coverage` module) — **2043 passed, 14 skipped**.