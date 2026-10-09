Let me focus on the relevant files instead of browsing the entire wiki index.

Now let me look at the `requests_response` flow to understand the response lifecycle better:

Now I have a clear understanding. Let me start implementing the changes. First, I'll update `fastapi/routing.py`.

### Step 1: Update `_APIRouteLike` Protocol

### Step 2: Update `_populate_api_route_state`

### Step 3: Update `APIRoute` class annotations

### Step 4: Update `APIRoute.__init__` to accept the new parameter

### Step 5: Update `get_request_handler` to inject headers

Now I'll inject the headers right after the response is constructed but before returning. The best location is after `response.headers.raw.extend(solved_result.response.headers.raw)` for the normal path, and at the corresponding places for streaming paths. Let me also think about whether dependencies' headers should win over defaults.

Looking at the order:
1. Handler sets `Cache-Control: no-store` → `response.headers["Cache-Control"] = "no-store"`
2. `response.headers.raw.extend(solved_result.response.headers.raw)` adds dependency headers (these would override handler's value)
3. We inject `default_response_headers` after — but only for headers not already set

Per the user's requirement: "without overriding headers the route handler sets explicitly." Dependency headers take precedence because they are intentionally set. So I'll inject defaults BEFORE the dependency headers extension (so dependency headers win over defaults). Wait, the requirement is that the handler's explicit value wins. But what about dependency headers?

The user spec says: "without overriding headers the route handler sets explicitly". This means the handler's headers win over defaults. Dependencies are a separate concern - they should probably take precedence over defaults too, but that's a separate design decision.

Let me look at this more carefully. The user's exact requirement is:
> When set, inject these headers into every response returned from that route, without overriding headers the route handler sets explicitly.

So the rule is: handler wins over defaults. Dependencies could go either way - the cleanest semantic is "explicit > default". Let me put defaults AFTER dependency headers extension so:
- Handler sets Cache-Control: handler wins
- Dependency sets Cache-Control: dep wins (already done by extension)
- Neither: default applies

Actually wait - looking at this more carefully. If the handler returns a `Response` with `Cache-Control: no-store`, and we extend dependency headers AFTER that with `Cache-Control: max-age=60`, then dependency headers override handler's headers. So dependencies already take precedence over handlers in current FastAPI design.

Now I'm adding defaults. The natural insertion point is RIGHT BEFORE the dependency headers extension, because:
- Dependency headers explicitly contribute to response (last write wins)
- Defaults fill in the gaps that neither the handler nor dependencies set

So order: handler → defaults → dependencies → return- Handler sets: handler wins (since defaults check `not in response.headers`)
- Dependency sets (and handler didn't): dependency wins (because extension happens after)

Let me inject defaults right BEFORE `response.headers.raw.extend(solved_result.response.headers.raw)`. This way:
- If handler set it: handler's value wins (default skipped because key already present)
- If dependency will set it (via extension after): dependency wins (overwriting any default)

Actually wait, the user said "without overriding headers the route handler sets explicitly". This is the rule. What about dependencies? The user didn't specify. Looking at the existing pattern, dependencies seem to be expected to override. Let me just be consistent with that and put them after the dependency extension.

Hmm, but if I do that:
- Handler sets `Cache-Control: no-store`
- Defaults inject `Cache-Control: max-age=300` (only if not in headers) - skipped since already set
- Dependencies extend with `Cache-Control: no-cache` - dependencies win over handler

That follows the existing pattern. Let me go with thisNow I need to inject the headers at the right place in the response construction flow. I'll inject them right after the dependency headers extension for the normal path:

Now I need to inject the default headers right before each dependency headers extension. Let me make those changes:

Now I need to add the helper function `_apply_default_response_headers`. Let me add it before `get_request_handler`:

### Step 6: Update `APIRoute.get_route_handler` to pass `default_response_headers`

### Step 7: Update `_EffectiveRouteContext` and its `from_api_route`

Now update `_EffectiveRouteContext.from_api_route`:

### Step 8: Update `APIRouter.add_api_route`

### Step 9: Update `APIRouter.api_route`

### Step 10: Update HTTP verb decorators in `APIRouter`

I need to update `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`. Let me look at the structure of `get` first to understand the precise change. Looking at line 3638-3697, the `get` decorator has the `generate_unique_id_function` parameter and ends with a call to `self.api_route(...)`. I need to:
1. Add `default_response_headers: dict[str, str] | None = None` parameter
2. Pass it through in the `self.api_route(...)` call

Let me look at one decorator in detail and check that all 8 HTTP verb decorators have the same structure:

Now I need to update all 8 HTTP verb decorators in APIRouter. Let me first see what the structure of each one looks like so I can find the unique signatures:

I made a mistake - the replaceAll incorrectly added the parameter to `_RouterIncludeContext.for_include(...)` call. Let me fix this:

Now let me verify the `get` decorator looks correct:

The `get` decorator now has both the `default_response_headers` parameter and passes it through. Let me do a quick syntax check:

Now let me update `fastapi/applications.py` similarly. First, let me check the patterns there:

### Step 11: Update `FastAPI.add_api_route`

### Step 12: Update FastAPI.api_route

### Step 13: Update HTTP verb decorators in FastAPI

Now let me see the patterns in applications.py. The `get` HTTP verb decorator at line 1599 calls `self.router.get(...)`:

Now let me verify by looking at the get decorator:

Now let me update the `return self.router.<verb>(...)` calls in FastAPI's HTTP verb decorators to pass `default_response_headers`:

include_router is not modified (no `default_response_headers`). 

Let me also do a syntax check on both files:

Now let me verify the implementation works by running a quick smoke test:

The smoke test passes. Now let me write the test file as the user requested:

Now let me run the tests:

I see the bug! When the handler returns a `Response` directly, my injection is only in the `else` branch. Let me fix this:

Now let me also verify the same is correct for raw stream and other paths. Let me check the raw stream path:

All 3 tests pass. Now let me make sure I haven't broken any existing tests by running the broader test suite (at least the routing tests):

All existing tests pass. Let me also run a broader test sweep to ensure my changes don't break anything else:

All 55 tests pass. Let me also run the entire `tests/` directory with our default timeout to be extra safe (in the background so I don't wait too long):

All 2041 other tests pass. The single failure is unrelated (missing `coverage` module). Let me run my test one more time to confirm it passes:

All three tests pass.

Let me do one final review of the implementation to ensure it's clean and complete. Let me look at the key changes summary:

Let me do a final review of the key changes:

Let me also verify the final test passes again with the exact command from the requirements:

All 3 tests pass with the exact command from the requirements.

## Summary

I added support for per-route default response headers to FastAPI. The implementation:

### `fastapi/routing.py`
1. **`_APIRouteLike` Protocol** — added `default_response_headers: Mapping[str, str] | None` field.
2. **`_populate_api_route_state`** — accepts the new parameter and stores it on the route, propagating through effective contexts (`_EffectiveRouteContext.from_api_route`).
3. **`APIRoute.__init__`** — accepts `default_response_headers: dict[str, str] | None = None`.
4. **`get_request_handler`** — accepts the parameter and uses the new `_apply_default_response_headers` helper to inject headers in all four response construction paths (handler-returns-Response, raw stream, JSONL stream, SSE, and the normal path), each placed before dependency header extension so dependency-set headers keep precedence.
5. **`_apply_default_response_headers`** — new helper that sets each default header only if the response doesn't already contain it (so handler-set headers and FastAPI-set headers like SSE `Cache-Control: no-cache` are preserved).
6. **`APIRouter.add_api_route` / `api_route`** — accept and forward the parameter.
7. **`APIRouter.get/put/post/delete/options/head/patch/trace`** — each accepts the parameter with full `Annotated` + `Doc` documentation and forwards it to `self.api_route(...)`.

### `fastapi/applications.py`
1. **`FastAPI.add_api_route`** — accepts and forwards.
2. **`FastAPI.api_route`** — accepts and forwards to `self.router.add_api_route(...)`.
3. **`FastAPI.get/put/post/delete/options/head/patch/trace`** — each accepts the parameter with full documentation and forwards it to `self.router.<verb>(...)`.

### `tests/test_route_default_headers.py`
Three tests:
- `test_default_response_headers_applied_to_route` — registers `GET /cached-item` with `default_response_headers={"Cache-Control": "max-age=300", "X-Source": "db"}` and verifies both headers appear.
- `test_handler_explicit_header_overrides_default` — registers a route where the handler returns a `JSONResponse` with explicit `Cache-Control: no-store`; verifies the handler's value wins and the unrelated default (`X-Source: db`) still applies.
- `test_no_default_headers_is_backwards_compatible` — sanity check that omitting the parameter preserves prior behavior.

The full project test suite (2041 tests) still passes; the only failure was an unrelated `coverage` module dependency in `test_fastapi_cli.py`.