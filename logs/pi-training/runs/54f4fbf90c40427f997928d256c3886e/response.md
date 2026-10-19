Let me look at the FastAPI class and routing code directly:

Let me look at the FastAPI class and the relevant sections of applications.py:

Now let me look at the decorator helpers (get, post, put, add_api_route):

Let me look at the `get` decorator and also the section around line 1883 (where `response_model_exclude_none` is mentioned):

Now let me look at the routing.py to see how `APIRoute` consumes `response_model_exclude_none`:

Let me look at the APIRoute class definition:

Let me check how `Default` sentinel works in this codebase:

Let me re-check the line numbers for the decorator methods:

Let me look at the test files to understand the testing patterns used:

Let me check if `applications.py` already uses any sentinel patterns and check imports:

Let me check existing tests that test similar app-level defaults:

Now I have a clear understanding. Let me implement the changes. First, let me add the sentinel:

Now let me add the `default_response_model_exclude_none` parameter to `FastAPI.__init__()`. Let me find the exact location:

Now let me add the storage line in the `__init__` body. Let me find a good location:

Now let me update the route decorator helpers. Let me start with `add_api_route`:

Now let me look at each of these decorator methods in turn to make targeted edits:

Now let me modify `api_route`:

Now let me find and modify the `get`, `put`, `post`, `delete`, `patch`, `head`, `options`, `trace` methods. First, let me find the exact `response_model_exclude_none: Annotated[` blocks:

Since all 8 decorator methods have identical parameter blocks, let me use a Python script to do a bulk replace:

Now let me look at the body of each decorator method to add the check. Let me first check the `get` method body:

The indentation broke. Let me look at the actual current state and fix it:

Let me look at the raw bytes to see what happened:

I see the bug - the leading 8 spaces before `return` are missing. Let me revert all 8 broken sections and re-do them properly. Let me undo the bad changes:

Now let me re-apply the changes correctly:

Let me verify the changes:

I see the issue - I have extra indentation. The 8 spaces in the captured group already provides the indentation. Let me fix:

Now let me re-apply the correct change:

Now let me verify:

Now the indentation is correct. Let me also verify the parameter signature for one of the methods:

Let me also verify all 8 decorator methods are updated and check the FastAPI `__init__`:

All 10 methods have the `_UNSET` default. Now let me also verify the `__init__` parameter and storage:

Now let me run a quick sanity check by importing and instantiating FastAPI:

Now let me run the existing tests to make sure I haven't broken anything:

Let me check if these failures are pre-existing by reverting my changes temporarily:

The existing test `test_signatures_consistency` enforces that `FastAPI.get` has the same default as `APIRouter.get` for all parameters. Since I'm adding a legitimate app-level default that differs from the route-level default for `response_model_exclude_none`, this test needs to be updated to allow this exception. Let me look at the test once more:

Let me update the test to allow the legitimate difference for `response_model_exclude_none`:

The annotations differ because I added a line to the docstring. Let me revert that docstring change so the annotations stay identical:

Let me run the broader test suite to find any other regressions:

All tests pass. Let me also check the tutorial tests:

Now let me write the test file `tests/test_global_exclude_none.py`:

Now let me run the tests as requested:

All 6 tests pass. Let me also run the full test suite to confirm no regressions:

Good. Let me run the requested test command once more to confirm:

Now let me run `graphify update` to keep the knowledge graph current as recommended by AGENTS.md:

`graphify` CLI is not available, so I'll skip that step. Let me verify all changes are in place and the module imports correctly:

Let me also do an end-to-end verification by running the test with verbose output one more time:

All 6 tests pass. Let me also do a final summary by reviewing the diff:

All changes are in place. Let me do a final comprehensive test run to confirm:

All 32 tests pass (3 skipped due to missing optional `orjson`). Let me also confirm the requested test command works:

All requirements are met. Here's a summary of what was done:

## Summary

### `fastapi/applications.py`

1. **Added a private `_Unset` sentinel class** (`_UNSET` instance) at the top of the file, used to detect whether `response_model_exclude_none` was explicitly passed by the caller or left to default.

2. **Added `default_response_model_exclude_none: bool = False`** to `FastAPI.__init__()`, with a full `Doc()` string and stored it as `self.default_response_model_exclude_none`.

3. **Modified all 10 path-operation entry points** on `FastAPI` (`add_api_route`, `api_route`, `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`):
   - Changed `response_model_exclude_none: bool = False` → `response_model_exclude_none: bool = _UNSET` in the signature.
   - At the start of each method body, added:
     ```python
     if response_model_exclude_none is _UNSET:
         response_model_exclude_none = self.default_response_model_exclude_none
     ```
   This means a per-route explicit value (e.g. `@app.get(..., response_model_exclude_none=False)`) still wins, while routes that don't set the parameter fall back to the app-level default.

### `tests/test_operations_signatures.py`

Updated the `test_signatures_consistency` test to exempt `response_model_exclude_none` from the strict default-value comparison (the annotation still stays in sync). A comment explains why this parameter legitimately differs.

### `tests/test_global_exclude_none.py` (new)

6 tests covering all required scenarios:
- The new attribute defaults to `False`.
- The attribute is stored on the app.
- `default_response_model_exclude_none=True` causes `None` fields to be dropped.
- `default_response_model_exclude_none=False` (default) keeps `None` fields.
- A per-route `response_model_exclude_none=False` overrides the app-level `True` only for that route.
- The app-level default propagates to all HTTP method decorators (POST, PUT, …), not just GET.

### Test result

`pytest -W ignore tests/test_global_exclude_none.py` → **6 passed**.