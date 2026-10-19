Now I have a clear understanding of the codebase. Let me make the changes. First, I'll add the `get_value_or_default` import.

Now let me add the `default_response_model_exclude_none` parameter to `__init__` and store it.

Now let me add the `self.default_response_model_exclude_none` assignment in the `__init__` body. Let me find a good spot:

Now let me change the simple signature in `add_api_route` and `api_route`. These are non-Annotated `bool = False`:

Now let me change the Annotated block. Since the 8 occurrences are identical, I can use `replaceAll: true`:

Now let me update all the call sites to resolve the default value:

Let me verify the indentation and check each call site is correct:

The indentation is wrong in the `api_route` method. Let me fix that:

Now let me verify all the call sites are correct:

Let me also verify the `__init__` signature has the new parameter:

Now let me run a quick test to verify the code still imports correctly:

Now let me write the test file:

Now let's run the tests:

I need to fix the call site resolution. The `get_value_or_default` returns a `DefaultPlaceholder` only when all inputs are `DefaultPlaceholder`s. When the user passes an explicit bool, the function returns the bool directly. Let me update the resolution logic:

Let me fix the call sites using an inline conditional. First, let me revert the import:

Now let me update all 10 call sites to use the inline conditional:

Now let's run the tests:

All 3 tests pass. Let me also run the existing related tests to make sure I didn't break anything:

All existing related tests pass. Let me also run the test from the task's command:

Let me run a wider set of tests to make sure I haven't broken anything:

The errors are due to missing optional dependencies. Let me run a more focused set:

All 83 tests pass. Let me also run a broader set to be extra sure my changes don't break anything:

Let me check the signature consistency test:

I need to also update the `APIRouter` methods to keep the signatures consistent. Let me check those:

Let me check the imports in routing.py:

Now I need to update the `APIRouter` methods to keep signatures consistent with FastAPI. Let me update them:

Now let me update the Annotated blocks in APIRouter:

Now let me update the call site in `APIRouter.add_api_route` to resolve the value before passing to `route_class`:

Now let me run the signature test:

Now let me run all the tests:

Let me also run a wider set of tests to make sure my changes to `APIRouter` don't break anything:

The only failing test is `test_fastapi_cli.py` which is unrelated to my changes (it requires `fastapi[standard]` which isn't installed). Let me verify this by checking the git log to make sure I didn't accidentally break it:

Confirmed - the `test_fastapi_cli` failure is pre-existing and unrelated to my changes. Let me check the diff of my changes:

The `.opencode/skills/graphify/SKILL.md` was modified by a hook (unrelated to my changes). Let me revert that:

Now let me view the complete diff to make sure my changes look correct:

Let me run the exact command from the task:

All 3 tests pass. Let me also run the full set of related tests:

All 91 related tests pass. Let me also run a broader test suite:

Excellent! 2040 tests pass, and the only failure is the pre-existing `test_fastapi_cli` which is unrelated to my changes (it needs `fastapi[standard]` which is an optional dependency not installed).

Let me run the exact test command from the task once more to confirm:

All 3 tests pass with `-v` showing each test name. Let me provide a summary of the changes:

## Summary

I added a global app-level default for `response_model_exclude_none` to FastAPI.

### Changes to `fastapi/applications.py`

1. **`FastAPI.__init__` signature** — added a new `default_response_model_exclude_none: bool = False` parameter (right before `**extra`) with a docstring explaining that it's used when a path operation doesn't pass `response_model_exclude_none` explicitly.

2. **`FastAPI.__init__` body** — stores the value as `self.default_response_model_exclude_none`.

3. **All 10 decorator/route helpers** on `FastAPI` (`add_api_route`, `api_route`, `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`):
   - Changed `response_model_exclude_none: bool = False,` (or the `Annotated` form) to `response_model_exclude_none: bool | DefaultPlaceholder = Default(False),` so the framework can distinguish "user did not pass" from "user passed `False`".
   - At the call site, the value is resolved: if the user passed an explicit `bool`, it's used as-is; otherwise the app-level `self.default_response_model_exclude_none` is used. This makes per-route explicit values still override the app-level default.

### Changes to `fastapi/routing.py` (for signature consistency)

To keep `FastAPI.get/put/...` and `APIRouter.get/put/...` signatures aligned (the `test_operations_signatures` test enforces this), the same `bool | DefaultPlaceholder = Default(False)` signature change was applied to `APIRouter.add_api_route`, `APIRouter.api_route`, the 8 `APIRouter` HTTP method decorators, and `APIRoute.__init__`. In `APIRouter.add_api_route`, the value is resolved to a plain `bool` before being passed to the `APIRoute` constructor (which still expects `bool`). `APIRouter` itself does **not** propagate an app-level default — it just accepts the union type so callers (e.g. `FastAPI.get`) can pass either an explicit `bool` or a `Default` placeholder.

### New test file: `tests/test_global_exclude_none.py`

Three tests, all passing:
- `test_app_default_true_excludes_none_fields_from_route_response` — `FastAPI(default_response_model_exclude_none=True)` with a route returning a model with `None` fields; verifies the JSON does not contain the `None` keys.
- `test_app_default_false_keeps_none_fields_in_route_response` — `FastAPI()` (default `False`); verifies `None` fields ARE included.
- `test_per_route_explicit_false_overrides_app_default_true` — on a `True`-default app, one route uses the default (None excluded) and another route uses explicit `response_model_exclude_none=False` (None included). This proves per-route explicit values still override the app-level default.

### Test result

```
tests/test_global_exclude_none.py ...                                    [100%]
============================== 3 passed in 0.25s ===============================
```