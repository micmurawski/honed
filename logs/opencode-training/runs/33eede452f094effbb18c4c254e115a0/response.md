Let me also check the `__init__` `extra` parameter placement to understand where to insert the new parameter:

Now I have a clear picture. Let me check the graphify index for any quick insight, then implement.

Now I have enough context. Let me implement the changes. First, I'll modify `FastAPI.__init__()` to add the new parameter:

Now let me add the `self.default_response_model_exclude_none` assignment in the `__init__` body. I'll add it right after the existing assignments, alongside other similar config:

Now I need to modify each decorator method. Let me start with `add_api_route`:

Now let me update `api_route`:

Now let me update the `get` method. I'll change the type annotation and default value, then add the resolution before the router call:

Let me make smaller, more targeted edits. First, change just the type and default for `response_model_exclude_none`:

Let me try smaller incremental edits:

There are 8 similar patterns. Let me use `replaceAll: true` for the type and default change since they all have the same pattern:

Now let me add the resolution check before each `return self.router.<method>(...)` call. Let me first check the current state:

Now let me add the resolution check for each method. I'll start with the `get` method:

Let me add the resolution for `put`:

Now let me find and update the `post` method:

Now let me find and update `delete`:

Now let me find and update `options`:

Now let me find and update `head`:

Now let me find and update `patch`:

Now let me find and update `trace`:

Let me verify all the resolution checks are in place:

All 10 methods have the resolution check. Let me also verify the `__init__` parameter was added correctly:

Let me verify the imports are correct:

Let me verify the type changes by checking a few of the methods:

Let me do a quick syntax check:

Now let me write the test file. First let me check the existing test conventions:

Now let me create the test file:

Now let's run the tests:

All 5 tests pass. Let me also run the related existing tests to make sure I haven't broken anything:

Let me run a broader set of related tests:

All 82 related tests pass. Let me run the new test one more time with verbose output to confirm everything is in order:

All 5 tests pass. Let me also update the graphify graph since the AGENTS.md asks to keep it current:

Let me give a final summary by reviewing the key changes:

All the changes are implemented and tested. Here's a summary of what was done:

## Summary

### Changes to `fastapi/applications.py`

1. **Added `default_response_model_exclude_none` parameter to `FastAPI.__init__()`** (after `strict_content_type`, before `**extra`) with a `bool` annotation, full docstring, and `False` default.

2. **Stored it as an instance attribute** alongside other config attributes:
   ```python
   self.default_response_model_exclude_none = default_response_model_exclude_none
   ```

3. **Modified all 10 path-operation methods on `FastAPI`** (`add_api_route`, `api_route`, `get`, `post`, `put`, `delete`, `options`, `head`, `patch`, `trace`):
   - Changed the parameter signature from `response_model_exclude_none: bool = False` to `response_model_exclude_none: bool | DefaultPlaceholder = Default(False)`. The `Default` sentinel allows us to detect "not passed" vs "passed `False`" — the same pattern already used for `response_model`, `response_class`, etc.
   - Added a resolution step right before forwarding to the router:
     ```python
     if isinstance(response_model_exclude_none, DefaultPlaceholder):
         response_model_exclude_none = self.default_response_model_exclude_none
     ```
   This ensures per-route explicit values override the app-level default, and the underlying `routing.APIRoute` only ever receives a concrete `bool`.

### New test file: `tests/test_global_exclude_none.py`

Five tests covering:
- `test_default_response_model_exclude_none_true_excludes_none_fields` — `default_response_model_exclude_none=True`, route returns model with `None` fields → JSON omits those keys.
- `test_default_response_model_exclude_none_false_includes_none_fields` — default app, route returns model with `None` fields → JSON includes `None` keys.
- `test_default_response_model_exclude_none_true_explicit_override_on_route` — on a `True`-default app, one route without override excludes `None`; a second route with explicit `response_model_exclude_none=False` includes them.
- `test_default_response_model_exclude_none_attribute_default_is_false` — attribute is `False` by default.
- `test_default_response_model_exclude_none_attribute_when_set_true` — attribute reflects `True` when set.

### Test result
```
tests/test_global_exclude_none.py::test_default_response_model_exclude_none_true_excludes_none_fields PASSED
tests/test_global_exclude_none.py::test_default_response_model_exclude_none_false_includes_none_fields PASSED
tests/test_global_exclude_none.py::test_default_response_model_exclude_none_true_explicit_override_on_route PASSED
tests/test_global_exclude_none.py::test_default_response_model_exclude_none_attribute_default_is_false PASSED
tests/test_global_exclude_none.py::test_default_response_model_exclude_none_attribute_when_set_true PASSED
============================== 5 passed in 0.31s ===============================
```

I also re-ran the related existing suites (`test_skip_defaults.py`, `test_default_response_class*.py`, `test_application.py`, all `test_response_model_*.py`, `test_serialize_response_model.py`) — 82 tests, all passing — and updated the `graphify-out/` graph (per `AGENTS.md`).