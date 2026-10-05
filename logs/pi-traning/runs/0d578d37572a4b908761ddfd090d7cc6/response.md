Now let me look at the APIRouter class and the relevant portions:

Now let me look at the route decorator methods (get, post, etc.):

Now let me find the `api_route` method which is used by all the decorators:

Now let me look at the OpenAPI utils.py to understand how the operation is generated:

Now let me look at applications.py to find the route methods on FastAPI:

Let me look at the rest of the HTTP method decorators in applications.py to understand their structure:

Now I have a good understanding. Let me look at the existing test file to understand the test pattern:

This is a perfect reference. Now let me look at the doc on `Annotated` import to ensure consistency:

Now let me look at the test patterns in test_application.py to see how they generally setup tests:

Let me also look at the post/delete/patch/etc. HTTP method decorators in `applications.py` to see what they look like, to determine the proper structure to use. Since the patterns are very similar, I'll mainly need to add the `rate_limit` parameter to each:

Now I have a clear understanding of the structure. Let me also check what comes after `openapi_extra` for the other HTTP method decorators (delete, patch, options, head, trace). I'll use a search approach:

Let me check if there's a delete/patch/etc. method in APIRouter that has `rate_limit` already:

Let me also look at how `openapi_extra` is documented in `applications.py` `get` method to find a clean way to insert the `rate_limit` parameter:

Now I have a complete picture. Let me also check whether `openapi_extra` in APIRoute's `__init__` uses the same parameter signature in `add_api_route` in `routing.py` vs. how it's used in `__init__`. I noticed `APIRouter.add_api_route` does NOT use `openapi_extra` directly but passes it. Let me check again:

Good. Now I have a complete picture. Let me start making the changes.

## Plan

I need to:

1. **`fastapi/routing.py`**:
   - Add `rate_limit: str | None = None` parameter to `APIRoute.__init__()`.
   - Pass it through `_populate_api_route_state` and store on the route.
   - Add the `rate_limit` field to `_APIRouteLike` Protocol and the type annotation for `APIRoute`.
   - Add the field to `_EffectiveRouteContext` dataclass.
   - Add `rate_limit: str | None = None` parameter to `APIRouter.add_api_route()` and propagate it to `route_class()`.
   - Add `rate_limit` parameter to `APIRouter.api_route()` and `APIRouter.get/post/put/delete/patch/options/head/trace` (or at least the ones that take similar params).
   
2. **`fastapi/applications.py`**:
   - Add `rate_limit: str | None = None` parameter to `FastAPI.get/post/put/delete/patch/options/head/trace/add_api_route/api_route`.
   - Propagate to `self.router.*`.

3. **`fastapi/openapi/utils.py`**:
   - In `get_openapi_path()`, when `route.rate_limit` is set, add `"x-rate-limit": route.rate_limit` to the operation.

4. **Tests**: Create `tests/test_rate_limit_metadata.py`.

Let me also check that `_iter_routes_with_context` works properly — but the existing flow looks solid, the `get_openapi_path` is the main function that builds the operation, and that's where we'll add the extension.

Let me look at the existing test pattern (test_openapi_route_extensions.py) for reference. I also need to know what `_iter_routes_with_context` is doing to understand if we need to add `rate_limit` to `_EffectiveRouteContext`:

Let me check the route context usage to understand whether I need to add `rate_limit` there:

Good, I see that `_EffectiveRouteContext` is what eventually gets passed to `get_openapi_path`, and it's populated via `_populate_api_route_state`. So I need to:

1. Add `rate_limit` to `_APIRouteLike` Protocol
2. Add `rate_limit` to `APIRoute` class type annotations
3. Add `rate_limit` parameter to `APIRoute.__init__()` and pass it to `_populate_api_route_state`
4. Add `rate_limit` parameter to `_populate_api_route_state` and set on the route
5. Add `rate_limit` field to `_EffectiveRouteContext` dataclass
6. Add `rate_limit` parameter to `APIRouter.add_api_route()` and pass to `route_class()`
7. Add `rate_limit` parameter to all HTTP method decorators in APIRouter (`get`, `post`, etc.) and `api_route()`
8. Add `rate_limit` to all HTTP method decorators in `FastAPI` (in `applications.py`)
9. In `openapi/utils.py`, in `get_openapi_path()`, when `route.rate_limit` is set, add `x-rate-limit` to operation

Let me start with `fastapi/routing.py`:

### 1. Add `rate_limit` to `_APIRouteLike` Protocol

Let me look at the two occurrences:

I need to be more specific. The first occurrence at line 936 is in `_APIRouteLike` protocol. The second at 1142 is in `APIRoute` class type annotation. Let me be more specific:

Now let me update the `_APIRouteLike` Protocol:

The two sections are very similar. Let me check the indentation of each:

Both have the same indentation. Let me use unique context using surrounding lines:

Let me use a larger unique context. First, the lines preceding each occurrence:

I see — the lines before the protocol include `path: str` and `endpoint: Callable[..., Any]` which differs. Let me use a more specific multi-line context for the first occurrence:

Let me do it differently. Since the issue is identical text appearing twice, I'll use context to disambiguate. Let me approach this differently — edit each block separately with unique surrounding context:

I'll add `rate_limit: str | None` after `strict_content_type` and before `tags` in both locations using unique context. The Protocol version follows `class _APIRouteLike(Protocol):` while the APIRoute class version follows `class APIRoute(routing.Route):`. Let me use this to disambiguate. But they are far apart, so using just lines from each should work if I include enough context. Let me do it in two separate edits using larger context:

OK, both blocks have exactly the same content from `stream_item_type: Any | None` to `responses: dict[int | str, dict[str, Any]]`. Let me use a different approach — make the changes using sed via a Python script, or use a much larger context:

The issue is that the class declarations differ. Let me use a different approach — make both edits in one edit with the full class name as part of the context. The first block starts with `class _APIRouteLike(Protocol):` and the second with `class APIRoute(routing.Route):`. I need to include enough surrounding context for each to be unique:

Now let me update `_populate_api_route_state` to accept and set the `rate_limit` parameter:

Now add the `rate_limit` parameter to `_populate_api_route_state` signature:

Now let me update the `APIRoute.__init__` to accept and pass `rate_limit`:

Now let me update the `_EffectiveRouteContext` dataclass to also include the `rate_limit` field. First let me find it:

Now let me check the `from_api_route` method of `_EffectiveRouteContext` to see if I need to update it to pass `rate_limit`:

I need to pass `rate_limit` here:

Now let me update `APIRouter.add_api_route()` to accept and pass `rate_limit`:

Now let me update `APIRouter.api_route()`:

Now I need to update each HTTP method decorator in `APIRouter`: `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`. Let me look at one of them first to understand the structure:

OK, so all HTTP method decorators in `APIRouter` end with `return self.api_route(path=path, ...)` and the final return value. Let me find the end of each decorator that uses `openapi_extra` to update them:

Let me look at the get decorator in `routing.py`:

Now let me look at all decorator signatures of APIRouter for the `openapi_extra` parameter:

Now let me see the structure to understand what each HTTP method decorator looks like. They all have the same `return self.api_route(path=path, ..., openapi_extra=openapi_extra, generate_unique_id_function=generate_unique_id_function,)` block. Let me update each one. I notice they're all unique because of the function name (`def put`, `def post`, etc.) - but the bodies are identical. Let me approach this carefully:

For each HTTP method decorator, I need to:
1. Add `rate_limit: str | None = None` parameter to the function signature
2. Pass `rate_limit=rate_limit` to the `self.api_route()` call

Let me look at the get method signature first:

OK, so all HTTP method decorators in `APIRouter` share the same signature pattern ending with `generate_unique_id_function: Annotated[Callable[[APIRoute], str], Doc(...)] = Default(generate_unique_id),`. The signature in `api_route` and these HTTP method decorators (in `routing.py`) does NOT have `Annotated[Callable[[APIRoute], str], Doc(...)]` for `generate_unique_id_function` because the typing in `api_route` is more plain (without Doc). So I need to be careful. Let me look at all the relevant sections more carefully. 

Let me check: how is `api_route` different from `get`/`post`/etc. method signatures? They have `Callable[[APIRoute], str]` without `Annotated` and `Doc`. Let me look:

Let me check the APIRouter `get` method signature:

So `get` is at line 3651, `put` at 4028, etc. Now I have a clear plan. Let me look at the typical signature end and `return` to see the structure:

So each HTTP method in APIRouter ends with `] = Default(generate_unique_id),\n    ) -> Callable[[DecoratedCallable], DecoratedCallable]:` then has its docstring. The HTTP method body calls `self.api_route(path=path, ..., openapi_extra=openapi_extra, generate_unique_id_function=generate_unique_id_function,)`. Let me add the `rate_limit` parameter to each. 

I'll need to do the same 2 edits per HTTP method: add parameter and pass it through. To handle this efficiently, let me find each function definition's end and the `self.api_route(...)` call.

Let me also look at where each method ends to understand the call:

Let me look at the lines for get/post/put etc methods:

Now let me look at the `get` method (in `routing.py`) structure. The end of its signature is right before the body. Let me find lines 3661-3667 (where `generate_unique_id_function` ends):

OK so each HTTP method in `routing.py` has this pattern. Let me look at what follows the docstring for `get`:

Now I have a clear pattern. For each HTTP method in `routing.py`, the body looks like:
```
        return self.api_route(
            path=path,
            ...
            openapi_extra=openapi_extra,
            generate_unique_id_function=generate_unique_id_function,
        )
```

And the methods differ only in `methods=["GET"]`, `methods=["PUT"]`, etc. Let me look at each `methods=` to see if they differ:

Each one has a unique `methods=` line. So I can use that to disambiguate. Let me make the changes one by one using `methods=["XXX"]` and the `openapi_extra` line as unique context:

Now let me update the `generate_unique_id_function` parameter signature. I need to add `rate_limit` parameter to the signature of each HTTP method (`get`, `put`, etc.) in APIRouter. The signature ends with:

```
        generate_unique_id_function: Annotated[
            Callable[[APIRoute], str],
            Doc(
                """
                Customize the function used to generate unique IDs for the *path
                operations* shown in the generated OpenAPI.

                This is particularly useful when automatically generating clients or
                SDKs for your API.

                Read more about it in the
                [FastAPI docs about how to Generate Clients](https://fastapi.tiangolo.com/advanced/generate-clients/#custom-generate-unique-id-function).
                """
            ),
        ] = Default(generate_unique_id),
    ) -> Callable[[DecoratedCallable], DecoratedCallable]:
```

Let me check if this pattern is identical for all 8 methods:

10 occurrences — 8 in HTTP method decorators, 1 in APIRouter.__init__, 1 in `include_router`. Let me check each:

I need to add the `rate_limit: str | None = None` parameter to each of the 8 HTTP method signatures. Let me add it after the `generate_unique_id_function` parameter (using a similar pattern as the `applications.py` `openapi_extra` parameter — but since this is in routing.py, I'll keep it simple without `Annotated`/`Doc` since `api_route` doesn't have them either). 

Let me look at what `api_route` has and follow the same convention. Looking at `api_route` (in `routing.py`) it has:

```python
generate_unique_id_function: Callable[[APIRoute], str] = Default(
    generate_unique_id
),
rate_limit: str | None = None,
```

So in `routing.py` HTTP method decorators, I should add the same convention. But in `applications.py`, they use `Annotated[..., Doc(...)]` because they have richer docstrings. Let me look at the existing applications.py `get` method to see what style is used:

OK, so in `applications.py` the convention is `Annotated[..., Doc(...)] = ...`. In `routing.py` it's the same for the HTTP method decorators. Let me add the `rate_limit` parameter using that convention. But this would be a very long Doc string for each method. 

Actually, I should match the convention. Let me look at what the `api_route` method in `routing.py` uses:

Looking at the current code in `routing.py`, `api_route` does NOT use `Annotated[..., Doc(...)]` — it's a simpler signature. So the same convention should be used for `rate_limit` in APIRouter's HTTP method decorators.

But wait — looking at the code I see that the `routing.py` HTTP method decorators (`get`, `put`, etc.) DO use `Annotated[..., Doc(...)]` style. So I need to match the existing convention. Let me look at how a parameter like `openapi_extra` is defined in APIRouter's `get` method:

OK so the `openapi_extra` in `routing.py` HTTP method decorators uses `Annotated[...]` with `Doc(...)`. Let me look at one of them:

Now I understand the pattern. Let me look at the structure of each HTTP method - the docstring text is identical across them. The lines for `Doc("..."""` text are different from APIRouter's `__init__` (line 2493) and `include_router` (line 3245).

Let me add the `rate_limit` parameter to each HTTP method in `routing.py`. The pattern to add is:
```
        rate_limit: Annotated[
            str | None,
            Doc(
                """
                A rate limit string (e.g. `"100/minute"`) to be added to the
                OpenAPI operation for this *path operation* as the
                `x-rate-limit` extension.

                It is not enforced by FastAPI itself; clients and gateway
                tooling can use the value documented in the OpenAPI spec.
                """
            ),
        ] = None,
```

The pattern to add is right after the `generate_unique_id_function` parameter. The end of the signature for each HTTP method is:

```
        ] = Default(generate_unique_id),
    ) -> Callable[[DecoratedCallable], DecoratedCallable]:
```

So I need to insert the `rate_limit` parameter before `] = Default(generate_unique_id),\n    ) -> Callable[[DecoratedCallable], DecoratedCallable]:` for each method. Since this text is identical across all 8 methods, I need to do them in batch with disambiguating context. Let me add some context — the line right before it is the Doc string closing, but the `Customize the function used to generate unique IDs` text only appears in the HTTP method decorators and in APIRouter init/include_router. 

Wait, I notice in the `routing.py` HTTP methods, the unique preceding text in each is the prior `methods=` call (in the body), but for the signature, each method's docstring ends differently. The cleanest way is to use the unique URL/Pydantic BaseModel reference at the end of the docstring of the body (e.g. `@router.get` vs `@router.put` examples). Let me see:

Each HTTP method has a unique example. But I can use the simpler approach of just appending a comment line or something distinctive. Let me approach this differently — I'll add the `rate_limit` parameter one at a time using the unique ending example. Let me look at where each method's body ends and where its signature ends:

The `] = Default(generate_unique_id),` line — for each method, it's followed by `) -> Callable[[DecoratedCallable], DecoratedCallable]:` and then a docstring starting with `"""Add a *path operation* using an HTTP XXX operation.`. Let me use that to disambiguate:

Now I can use the unique docstring text. Let me look at the end of each signature:

Now let me make all 8 edits in one go, each with the unique docstring text:

Now let me also update `APIRouter.api_route()` signature to add `rate_limit` parameter — it currently doesn't have `Annotated[..., Doc(...)]`, so I'll just add a simple parameter. Let me look at the current state:

I already added the `rate_limit` parameter to `api_route` earlier. Let me verify:

Good, `api_route` already has `rate_limit`. Now let me verify the changes to `routing.py` are syntactically valid by doing a quick syntax check:

Now let me update `fastapi/applications.py`. Let me look at the current `add_api_route` signature:

Now let me update the `api_route` method in applications.py:

Now let me update each HTTP method decorator in `applications.py`. There are 8 (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`). Let me first verify what they look like for `get`:

OK, so the `applications.py` HTTP methods follow the same pattern. I need to add the `rate_limit: Annotated[str | None, Doc(...)] = None` parameter. Let me check the lines for each method:

Let me add the `rate_limit` parameter to each HTTP method in `applications.py` using unique disambiguating context:

Now I need to update the body of each HTTP method to pass `rate_limit=rate_limit` to `self.router.X()`. Let me look at how the `get` method's body looks now:

Let me check the end of each `return self.router.X(...)` block:

I see the pattern. Each method has this body. Let me update each one to pass `rate_limit=rate_limit`. I'll use the same approach of finding the unique disambiguating context. Let me check the `openapi_extra=openapi_extra,\n            generate_unique_id_function=generate_unique_id_function,\n        )\n\n    def XXX(` pattern. Each method is followed by `    def <next_method>(` so I can use the next method's name to disambiguate:

The last edit (on_event) is the trace method's end. Let me check:

The trace method's body is followed by `def websocket_route`. Let me also re-look at the trace method's exact end since I may have miscounted:

The trace method ends with `        )\n\n    def websocket_route(`. Let me re-do the previous 7 edits that worked, then handle the trace method separately:

Now let me verify all 8 HTTP method bodies have been updated:

Hmm, only 3. Let me check:

Wait, `routing.py` has 10 instances of `rate_limit=rate_limit` (8 HTTP method decorators + add_api_route + api_route). But `applications.py` only has 3 — let me check what happened to the rest:

Looking back at the previous edit result, only 7 out of 8 edits succeeded. Let me check which HTTP method bodies weren't updated. Let me look at the area right before each method definition to see which are missing the `rate_limit=rate_limit` line in the call:

Let me check if all the methods now have the `rate_limit=rate_limit,` argument in the body:

None of the method bodies have `rate_limit=rate_limit` (other than the trace one which is the last one we manually added). Let me re-check the previous edit:

Right, the edits didn't apply. Let me check the previous edit history. The error message said "Could not find edits[7]" — the 8th edit failed. Let me look at the response more carefully — the previous batch edit reported 7 of 8 failed (with the last error). Let me re-apply the missing 7 edits. First, let me use a different approach by using the function name pattern directly. The issue was the entire pattern was identical — the 7 edits might have been processed in the wrong order. Let me check if they were actually applied:

OK, so only 3 in `applications.py` (the `add_api_route` at 1221, the `api_route` at 1359, and `trace` body at 4751). The 7 earlier edits (for get, put, post, delete, options, head, patch) did NOT apply. The error message from my previous attempt only mentioned the 8th edit. Let me re-do those 7 now:

Now let me also check if the `get` method got the body update applied. Let me check:

Now there are 9 in `applications.py` (2 in add_api_route/api_route + 7 HTTP methods = 9). I need to add one more for `get`:

So the `get` method body was already updated. Now let me look at the put method's body:

Good, the `put` is also updated. Let me look at all 8 HTTP methods in `applications.py` to verify:

7 out of 8. The `patch` method is missing! Let me look at that one:

I see, the patch method's body still doesn't have `rate_limit=rate_limit`. Let me fix it:

Let me verify all 8 HTTP methods in `applications.py` now have `rate_limit=rate_limit` in their body:

All 8 HTTP methods in `applications.py` have `rate_limit=rate_limit`. Let me also verify the signature has the `rate_limit` parameter:

All 8 HTTP method signatures have `rate_limit: Annotated[...]` parameter.

Now let me also check `routing.py`:

All 8 HTTP method signatures have `rate_limit: Annotated[...]` in `routing.py`. Now let me verify by running the syntax check:

Let me also do a quick test to make sure the parameter is accessible:

The `rate_limit` parameter is being correctly propagated. Now let me update the OpenAPI generation logic:

### 3. Update OpenAPI generation in `fastapi/openapi/utils.py`

I need to inject the `x-rate-limit` field into the operation. The best place is right after `if route.openapi_extra:` block, or right before. Let me add it just before `path[method.lower()] = operation`:

Now let me verify by running the basic test:

The OpenAPI generation works. Now let me also test with `add_api_route`:

Let me also test that it works with an APIRouter:

Let me also test POST, PUT, DELETE, PATCH:

All HTTP methods work. Now let me create the test file:

### 4. Create tests/test_rate_limit_metadata.py

Now let me write the test file:

Now let me run the tests:

All 4 tests pass. Now let me also run the existing tests to make sure I haven't broken anything:

Tests pass. The `inline-snapshot is not able to format your code` is just a warning unrelated to our changes. Let me also run the OpenAPI tests to make sure they pass:

These errors are about missing optional packages (a2wsgi etc.), unrelated to our changes. Let me run a smaller set of tests to verify:

All 140 openapi-related tests pass. Let me also run some other broader tests:

All 2040 tests pass. The one failure is `test_fastapi_cli` which is unrelated to our changes (it's a CLI test that fails due to a missing `coverage` module).

Let me also run the test in isolation as the user requested:

All 4 tests pass. Now let me also update the graph since the project instructions mention `graphify`:

Graph updated. Let me also verify everything one more time by looking at all the final changes:

Summary of changes:

- **`fastapi/routing.py`** (28 references): Added `rate_limit: str | None` field to `_APIRouteLike` Protocol, `APIRoute` class, and `_EffectiveRouteContext` dataclass. Added `rate_limit` parameter to `APIRoute.__init__()`, `_populate_api_route_state()`, `APIRouter.add_api_route()`, `APIRouter.api_route()`, and all 8 HTTP method decorators (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`).
- **`fastapi/openapi/utils.py`** (2 references): Added the `x-rate-limit` extension to the OpenAPI operation when `route.rate_limit` is set, just before the operation is added to the path.
- **`fastapi/applications.py`** (20 references): Added `rate_limit: Annotated[str | None, Doc(...)] = None` parameter to `FastAPI.add_api_route()`, `FastAPI.api_route()`, and all 8 HTTP method decorators (`get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`), with the value propagated to the underlying router.
- **`tests/test_rate_limit_metadata.py`** (3 references): New test file with 4 tests verifying:
  1. The `/limited` endpoint works.
  2. The `/unlimited` endpoint works.
  3. `x-rate-limit: "50/minute"` appears on the `/limited` operation in the OpenAPI schema.
  4. `x-rate-limit` is absent from the `/unlimited` operation in the OpenAPI schema.

Final test run:

All 4 tests pass with the requested `pytest -W ignore tests/test_rate_limit_metadata.py` command.