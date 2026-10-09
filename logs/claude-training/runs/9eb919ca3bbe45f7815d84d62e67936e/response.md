The `graphify query` command needs approval, so I'm reading the source directly instead.

Now the core edits in `routing.py`.

I couldn't make any changes: write permission for `fastapi/routing.py` was denied, so no files were modified and no tests were run. Running `graphify query` also needed approval, so I read the source directly instead. I won't try to get around the write denial through the shell.

**Change plan, ready to apply once edits are allowed:**

1. **Where the headers go in** (`fastapi/routing.py`, `get_request_handler`):
   - Add a `default_response_headers: dict[str, str] | None = None` parameter.
   - Insert the headers just before `return response` (around line 758). That is the one point every response passes through: normal, streaming, SSE, and `Response` objects the handler returns.
   - Only add a header if `header_name not in response.headers`. That lookup ignores letter case, so a header the handler set wins. This covers headers on a returned `Response` and headers set through an injected `response: Response` parameter.
2. **Carrying the value through the route** (`routing.py`):
   - Add the field to `_APIRouteLike` and to the `APIRoute` class annotations.
   - Add the parameter to `_populate_api_route_state`, which stores it on the route.
   - Add it to `APIRoute.__init__()` and pass it on.
   - Pass `route.default_response_headers` from `get_route_handler()`.
   - Add it to the `_EffectiveRouteContext` dataclass and copy it in `from_api_route`, so routes inside an `include_router(...)` router keep their headers.
3. **Decorator helpers:**
   - In `routing.py`: `APIRouter.add_api_route`, which passes it to `route_class(...)`, `api_route`, and `get/put/post/delete/options/head/patch/trace`. Each verb gets an `Annotated[..., Doc(...)]` parameter, matching the existing style.
   - In `fastapi/applications.py`: `FastAPI.add_api_route`, `api_route`, and the same eight verbs.
4. **Tests** (`tests/test_route_default_headers.py`):
   - `GET /cached-item` with `{"Cache-Control": "max-age=300", "X-Source": "db"}`; check that both headers come back.
   - A second endpoint that sets `response.headers["Cache-Control"] = "no-store"`; check that `no-store` wins and `X-Source` is still added.
5. Run `pytest -W ignore tests/test_route_default_headers.py`, then `graphify update .` as the project's CLAUDE.md asks.

To let me go ahead, allow edits to `fastapi/`, `tests/` and the `graphify` command (with `/permissions` or by switching to accept-edits mode), then re-run the request.