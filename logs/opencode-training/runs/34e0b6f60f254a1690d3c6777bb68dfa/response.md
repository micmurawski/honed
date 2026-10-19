Now let me look at the `_EffectiveRouteContext` and check how it handles path:

Now I have a thorough understanding. Let me plan my implementation:

I'll proceed with the assumption that the second route is registered as `/users2` (no slash), since that's the only interpretation consistent with your assertions (`/users2/` → 307 redirect to `/users2`, `/users2` → 200). Let me now implement the changes step by step.

First, let me add `import re` and a helper function for compiling paths with strict_slashes support:

Now let me add the helper function for compiling path with strict_slashes:

Now let me modify `_populate_api_route_state` to accept `strict_slashes`:

Now let me add `strict_slashes` to the `_APIRouteLike` Protocol:

Now let me find the APIRoute class and modify it:

Now let me modify `_RouterIncludeContext` to include `strict_slashes`:

Let me modify the `_RouterIncludeContext`:

Now let me modify `_EffectiveRouteContext`:

Now update `from_api_route` to pass strict_slashes:

Now let me modify `APIRouter.add_api_route`:

I need to make the router's `strict_slashes` propagate to routes by default. Let me use the `Default` pattern:

Now let me modify `APIRouter.__init__`:

Now let me find where `self.strict_content_type` is set in `APIRouter.__init__` and add `strict_slashes` next to it:

Now let me update `APIRouter.include_router` to accept and propagate strict_slashes:

Now let me update `FastAPI.include_router`:

Let me verify the implementation with a quick smoke test before writing the actual tests:

Let me verify the strict mode (default) and other scenarios:

The implementation works correctly. Let me now write the test file:

Now let me run the tests:

All 9 tests pass. Let me run the user's exact command:

Let me run the related test suites to make sure I haven't broken anything:

All 145 related tests pass. Let me also run a broader test sweep to catch any other potential regressions:

All 2048 tests pass with no regressions. Let me also update the knowledge graph per the project's AGENTS.md guidance:

Let me verify a few more edge cases:

Let me verify the complete final state and run the tests one more time:

Let me also verify the diff to confirm all changes are correct:

All implementation is complete. Let me run a final summary check:

All 9 tests pass with the user's exact command: `pytest -W ignore tests/test_strict_slashes.py`.

## Summary

I added router-level `strict_slashes` support to FastAPI. Here's what was changed:

### `fastapi/routing.py`
- **New helper `_compile_api_route_path()`**: compiles a path into a regex, optionally rewriting the regex so the trailing slash is optional when `strict_slashes=False`. The `path_format` is preserved as the originally registered path so URL generation and OpenAPI schema generation stay consistent.
- **`_APIRouteLike` Protocol**: added `strict_slashes: bool`.
- **`_populate_api_route_state()`**: new `strict_slashes: bool = True` parameter; uses the helper to build `path_regex`/`path_format`/`param_convertors`; sets `route.strict_slashes`.
- **`APIRoute`**: new class annotation + `__init__` parameter (`strict_slashes: bool = True`) that flows through to `_populate_api_route_state`.
- **`_RouterIncludeContext`**: new `strict_slashes: bool = True` field; `for_include` sets it from the parent router (AND'd with the include-level override); `combine` AND-combines the two contexts.
- **`_EffectiveRouteContext`**: new `strict_slashes: bool = True` field; `from_api_route` passes `route.strict_slashes` so the effective regex is rebuilt with the route's own setting (preserving leniency when the router is included).
- **`APIRouter.__init__`**: new `strict_slashes: bool = True` parameter (with docstring), stored as `self.strict_slashes`.
- **`APIRouter.add_api_route`**: new `strict_slashes: bool | DefaultPlaceholder = Default(True)` parameter; resolved via `get_value_or_default(strict_slashes, self.strict_slashes)` so the router's value becomes the route's default.
- **`APIRouter.include_router`**: new `strict_slashes: bool = True` parameter (with docstring), threaded into `_RouterIncludeContext.for_include`.

### `fastapi/applications.py`
- **`FastAPI.include_router`**: new `strict_slashes: bool = True` parameter (with docstring), forwarded to `self.router.include_router`.

### `tests/test_strict_slashes.py` (new)
9 tests covering:
- Lax router (`strict_slashes=False`) with `/users` matches both `/users` and `/users/` with 200 and no redirect.
- Strict router (default) with `/users2` returns 200; `/users2/` returns 307 → `http://testserver/users2` (or 404 if redirects are disabled).
- Default value of `APIRouter.strict_slashes` is `True`.
- Propagation from router → route via `add_api_route()`.
- Per-route override on `add_api_route(strict_slashes=...)`.
- Propagation through `include_router()` so routes added inside an included router match both slash variants.

### Note on test 2 ambiguity
Your spec reads "register a router with `strict_slashes=True` ... assert `/users2/` returns 307 or 404 but `/users2` returns 200." For that assertion to hold under FastAPI/Starlette's `redirect_slashes=True` default, the route must be registered as `/users2` (no trailing slash), so the redirect logic rewrites `/users2/` → `/users2` with status 307. I registered `/users2` accordingly; let me know if you instead wanted `/users2/` registered and I should change the test.

### Regression check
Ran the full `tests/` suite (excluding the unrelated tutorials that need `strawberry`): **2048 passed, 9 skipped, no failures.**