Now I have a complete picture. Let me start implementing the changes.

**Step 1: Update `fastapi/routing.py`**

First, add the type annotation for `include_in_schema_if` to the APIRoute class:

Now let me update `_populate_api_route_state`:

Now update `APIRoute.__init__()`:

Now I need to update the call to `_populate_api_route_state` in `APIRoute.__init__()`:

Now update `APIRouter.add_api_route()`:

Now update the route_class() call in add_api_route:

Now update `APIRouter.api_route()`:

Let me try a different approach. I'll do all edits in one call but with unique enough context to match each occurrence:

The challenge: 8 decorator methods have identical signatures up through `include_in_schema: Annotated[...] = True,`. Let me first check if there's a way to use a multi-line unique anchor for each.

Looking at the methods, they all end with the same closing `include_in_schema=include_in_schema,` line in the body. But the `methods=["X"]` line is unique per method and is just before the close of the body.

I'll use a strategy: edit only the body block (where `methods=["X"]` is the unique anchor), and edit only the signature block using the next method's signature as a unique anchor.

Wait, the signature has the SAME body block (calls `add_api_route` with `methods=["X"]`). The issue is the SIGNATURE - `include_in_schema: Annotated[bool, ...]` is identical for all 8 methods.

Let me just try a different strategy: I'll find the unique context by extending the edit text further. Looking at the include_in_schema block in the signature, the line right before is the closing `] = False,` of response_model_exclude_none. But that's also identical.

Actually, let me look at the block from `response_model_exclude_none: Annotated[` to the closing `] = True,` of include_in_schema. This block is identical in all 8 methods. But what's before `response_model_exclude_none:`? That's `response_model_exclude_defaults: Annotated[bool, ...]` with the same Doc text. Hmm, all the same.

OK, let me try a totally different approach. Let me write a single sed/python script to do these 16 edits programmatically:

Let me take a different approach. I'll use the `def <method>(\n        self,\n        path:` block as the unique anchor for the signature edit:

Let me take a more practical approach - I'll use a Python script to make these many edits programmatically:

Let me re-apply the edits to `_populate_api_route_state`, APIRoute, APIRouter.add_api_route, and APIRouter.api_route:

Now update APIRoute class attributes:

Now update `APIRoute.__init__`:

Now `APIRouter.add_api_route`:

Now `APIRouter.api_route()`:

Now verify by trying the import again:

Now let me update `fastapi/applications.py`. First let me look at the existing decorator methods:

Now update `FastAPI.api_route()`:

Now verify:

Now I need to add `include_in_schema_if` to the signatures of `FastAPI.add_api_route` and `FastAPI.api_route`:

**Step 3: Update `fastapi/openapi/utils.py`**

Now add the `_route_should_be_included` helper:

Now let me verify the implementation works:

Let me run the existing tests to ensure I didn't break anything:

**Step 4: Write the test file**

Now run the new test:

Let me fix the test by using a stateful predicate that can be patched:

Let me run a wider set of tests to make sure I didn't break anything else:

Now let me run the exact test command from the task:

Let me also run a quick mypy check on the modified files to make sure they still type-check:

Let me check the final state of the test file and verify the implementation summary:

Let me show a summary of all the changes:

All tests pass. Let me run one final verification of the specific test command from the task:

All tasks have been completed successfully:

## Summary

### `fastapi/routing.py`
- Added `include_in_schema_if: Callable[[], bool] | None` annotation to the `_APIRouteLike` protocol and `APIRoute` class.
- Added the parameter to `_populate_api_route_state()` and its assignment to `route.include_in_schema_if`.
- Added the parameter to `APIRoute.__init__()`, `APIRouter.add_api_route()`, and `APIRouter.api_route()`.
- Added the parameter to all 8 decorator methods (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`), with both signature (`Annotated[Callable[[], bool] | None, Doc(...)]`) and forwarding to `add_api_route()`.

### `fastapi/applications.py`
- Propagated `include_in_schema_if` through `FastAPI.add_api_route()`, `FastAPI.api_route()`, and all 8 decorator methods, each forwarding the value to `self.router.<method>`.

### `fastapi/openapi/utils.py`
- Added `_route_should_be_included(route)` helper which evaluates the predicate at schema generation time:
  - `False` if `include_in_schema` is `False` (preserves existing behaviour).
  - `True` if `include_in_schema_if` is `None` (falls back to the existing boolean, no behaviour change).
  - Otherwise calls `include_in_schema_if()` and returns its result.
- Replaced the two existing `if route.include_in_schema:` checks (`get_openapi_path`, `get_fields_from_routes`) with `if _route_should_be_included(route):`.

### `tests/test_include_in_schema_if.py`
- Registers `GET /public` (no condition) and `GET /feature-flag` with `include_in_schema_if=lambda: feature_flag_enabled`.
- `test_feature_flag_hidden_when_predicate_false` — verifies `/public` is in schema, `/feature-flag` is not.
- `test_feature_flag_visible_when_predicate_true` — uses `monkeypatch` to toggle the flag to `True`, regenerates the schema, and verifies both routes are present.

Test results:
```
$ pytest -W ignore tests/test_include_in_schema_if.py
============================== 2 passed in 0.27s ===============================
```

The full test suite (2039 tests, excluding unrelated tutorials and CLI tests) also passes with no regressions.