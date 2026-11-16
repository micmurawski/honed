Do not accept underscore headers when using `convert_underscores=True` (the default).

For example, this test should pass once it's done:

```python
from fastapi import Cookie, FastAPI, Header, Query

from fastapi.testclient import TestClient

from pydantic import BaseModel

app = FastAPI()

class Model(BaseModel):
    param: str

    model_config = {"extra": "allow"}

class AuthHeaders(BaseModel):
    x_user_id: str

@app.get("/query")
async def query_model_with_extra(data: Model = Query()):
    return data

@app.get("/header")
async def header_model_with_extra(data: Model = Header()):
    return data

@app.get("/cookie")
async def cookies_model_with_extra(data: Model = Cookie()):
    return data

@app.get("/header-requires-hyphen")
async def header_model_requires_hyphen(data: AuthHeaders = Header()):
    return data

def test_header_model_rejects_underscore_header_with_convert_underscores():
    client = TestClient(app)

    resp = client.get(
        "/header-requires-hyphen", headers={"x_user_id": "underscore-value"}
    )

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["header", "x_user_id"]
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
