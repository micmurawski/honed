I'm adding the request ID middleware now. Creating a new file in `fastapi/middleware/` wasn't approved, so I'll define the middleware inside `fastapi/applications.py` instead.

I couldn't make any changes. Writing a new file and editing `fastapi/applications.py` both need permissions this session doesn't have, and the `graphify query` the project instructions ask for was blocked too. Nothing has been modified, no test was written, and pytest hasn't been run.

Here is the change I'd make once file writes are allowed.

**1. New file `fastapi/middleware/request_id.py`**, written as plain ASGI middleware in the same style as `asyncexitstack.py`:

```python
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


# Generates a UUID4 per request, stores it in `request.state.request_id` and
# returns it in the response headers
class RequestIDMiddleware:
    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[self.header_name] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
```

**2. Changes to `fastapi/applications.py`:**
- Add the new parameter `add_request_id_header: Annotated[bool, Doc(...)] = False` just before `**extra` (around line 863).
- Store it with `self.add_request_id_header = add_request_id_header`.
- In `build_middleware_stack` (line 1033), add `Middleware(RequestIDMiddleware)` as the outermost layer when the flag is on. Placing it outside `ServerErrorMiddleware` means 500 error responses also get the `X-Request-ID` header.

**3. New test `tests/test_request_id_middleware.py`:** it creates `FastAPI(add_request_id_header=True)` with a route that returns `request.state.request_id`. It checks that `X-Request-ID` is in the response headers, that `uuid.UUID(value).version == 4`, that the header matches `request.state.request_id`, that two requests get different IDs, and that the header is missing when the flag is off.

To let me go ahead, allow Write/Edit for this worktree (for example via `/permissions`) and allow running `pytest` and `graphify`. Then I'll make the changes, run `pytest -W ignore tests/test_request_id_middleware.py` and `graphify update .`.

Separately, the claude.ai Gmail, Google Calendar and Google Drive connectors need to be authorized in your claude.ai connector settings before they can be used. This task doesn't need them.