graphify isn't approved in this session, so I'll read the relevant code directly.

I'll add the `__init__` parameter and store it, then switch every app-level route method to a `Default(False)` sentinel that's resolved against the app default.

Writing to `fastapi/applications.py` was denied. I'll check whether I can create the test file.

I couldn't make the change. The session was denied write permission for both `fastapi/applications.py` and `tests/test_global_exclude_none.py`, and it's non-interactive, so I couldn't ask for approval. `graphify query` was also denied, so I read the source directly. Nothing in the worktree changed and no tests ran.

**What I found:**
- `FastAPI.get` / `post` / `put` (and the other method decorators, `api_route` and `add_api_route`) default `response_model_exclude_none` to `False`. They pass it to `self.router`, which passes it to `APIRoute`, which uses it as `exclude_none=` when serializing (`fastapi/routing.py:514`, `:735`).
- With a plain `False` default, there's no way to tell whether the caller set it. FastAPI already has a marker for "the caller didn't pass this": `Default(...)`, checked with `get_value_or_default`. That's how `response_class` and `strict_content_type` inherit their defaults.

**What I'd change:**
1. **`FastAPI.__init__`:** add a documented `default_response_model_exclude_none: bool = False` parameter after `strict_content_type`, and store it with `self.default_response_model_exclude_none = default_response_model_exclude_none`.
2. **All 10 route methods** (`add_api_route`, `api_route`, `get`, `put`, `post`, `delete`, `options`, `head`, `patch`, `trace`): change the default from `False` to `Default(False)`. `Default()` is typed so the type hint can stay `bool`.
3. **Where each one passes the value to the router:** pass `get_value_or_default(response_model_exclude_none, self.default_response_model_exclude_none)` instead. An explicit `True` or `False` on a route still wins; otherwise the app default applies.

**Tests I prepared** for `tests/test_global_exclude_none.py` (also not saved), using an `Item` model with optional `description` and `price` fields:
- An app with `default_response_model_exclude_none=True`: `GET /item` returns `{"name": "Foo"}`.
- An app with the default `False`: the response includes `"description": None, "price": None`.
- On the `True` app, a second route with `response_model_exclude_none=False` returns the `None` fields while `/item` still leaves them out.

**One limitation:** routes added through `app.include_router(...)` won't pick up the app default. `APIRouter` already turns the value into a real `True`/`False` when the route is created, so the app can't tell whether it was set. Covering those routes would mean adding the same marker to `APIRouter` and `APIRoute` in `routing.py`.

If you allow edits to `fastapi/applications.py` and `tests/` (or re-run with edit permission), I'll apply this, run `pytest -W ignore tests/test_global_exclude_none.py`, and run `graphify update .`.