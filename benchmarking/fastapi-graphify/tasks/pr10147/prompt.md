Add WebSocket support to security dependencies.

For example, this test should pass once it's done:

```python
from fastapi import FastAPI, Security, WebSocket

from fastapi.security.http import HTTPAuthorizationCredentials, HTTPBase

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

app = FastAPI()

security = HTTPBase(scheme="Other", auto_error=False)

@app.get("/users/me")
def read_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
):
    if credentials is None:
        return {"msg": "Create an account first"}
    return {"scheme": credentials.scheme, "credentials": credentials.credentials}

@app.websocket("/users/timeline")
async def read_user_timeline(
    websocket: WebSocket,
    credentials: HTTPAuthorizationCredentials | None = Security(security),
):
    await websocket.accept()
    await websocket.send_json(
        {"scheme": credentials.scheme, "credentials": credentials.credentials}
        if credentials
        else {"msg": "Create an account first"}
    )

client = TestClient(app)

def test_security_http_base_with_ws_no_credentials():
    with client.websocket_connect("/users/timeline") as websocket:
        data = websocket.receive_json()
        assert data == {"msg": "Create an account first"}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
