Add Pydantic validation options to request parameters.

For example, this test should pass once it's done:

```python
from typing import Annotated

import pytest

from fastapi import FastAPI

from fastapi.testclient import TestClient

from inline_snapshot import snapshot

from pydantic import BaseModel

def test_openapi_schema(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    assert response.json() == snapshot(
        {
            "openapi": "3.1.0",
            "info": {"title": "FastAPI", "version": "0.1.0"},
            "paths": {
                "/pet/assignment": {
                    "post": {
                        "summary": "Create Pet Assignment",
                        "operationId": "create_pet_assignment_pet_assignment_post",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "anyOf": [
                                            {"$ref": "#/components/schemas/Cat"},
                                            {"$ref": "#/components/schemas/Dog"},
                                        ],
                                        "title": "Pet",
                                    }
                                }
                            },
                            "required": True,
                        },
                        "responses": {
                            "200": {
                                "description": "Successful Response",
                                "content": {"application/json": {"schema": {}}},
                            },
                            "422": {
                                "description": "Validation Error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/HTTPValidationError"
                                        }
                                    }
                                },
                            },
                        },
                    }
                },
                "/pet/annotated": {
                    "post": {
                        "summary": "Create Pet Annotated",
                        "operationId": "create_pet_annotated_pet_annotated_post",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {"$ref": "#/components/schemas/Cat"},
                                            {"$ref": "#/components/schemas/Dog"},
                                        ],
                                        "title": "Pet",
                                    }
                                }
                            },
                            "required": True,
                        },
                        "responses": {
                            "200": {
                                "description": "Successful Response",
                                "content": {"application/json": {"schema": {}}},
                            },
                            "422": {
                                "description": "Validation Error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/HTTPValidationError"
                                        }
                                    }
                                },
                            },
                        },
# …
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
