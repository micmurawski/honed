Fix `exclude_defaults` not propagated to dict keys and values in `jsonable_encoder`.

For example, this test should pass once it's done:

```python
import warnings

from collections import deque

from dataclasses import dataclass

from datetime import datetime, timezone

from decimal import Decimal

from enum import Enum

from math import isinf, isnan

from pathlib import PurePath, PurePosixPath, PureWindowsPath

from typing import TypedDict

import pytest

from fastapi._compat import Undefined

from fastapi.encoders import jsonable_encoder

from fastapi.exceptions import PydanticV1NotSupportedError

from pydantic import BaseModel, Field, ValidationError

class Person:
    def __init__(self, name: str):
        self.name = name

class Pet:
    def __init__(self, owner: Person, name: str):
        self.owner = owner
        self.name = name

@dataclass
class Item:
    name: str
    count: int

class DictablePerson(Person):
    def __iter__(self):
        return ((k, v) for k, v in self.__dict__.items())

class DictablePet(Pet):
    def __iter__(self):
        return ((k, v) for k, v in self.__dict__.items())

class Unserializable:
    def __iter__(self):
        raise NotImplementedError()

    @property
    def __dict__(self):
        raise NotImplementedError()

class RoleEnum(Enum):
    admin = "admin"
    normal = "normal"

class ModelWithConfig(BaseModel):
    role: RoleEnum | None = None

    model_config = {"use_enum_values": True}

class ModelWithAlias(BaseModel):
    foo: str = Field(alias="Foo")

class ModelWithDefault(BaseModel):
    foo: str = ...  # type: ignore
    bar: str = "bar"
    bla: str = "bla"

def test_encode_model_with_default_in_dict_and_list():
    model = ModelWithDefault(foo="foo", bar="bar")
    assert jsonable_encoder([model], exclude_defaults=True) == [{"foo": "foo"}]
    assert jsonable_encoder({"key": model}, exclude_defaults=True) == {
        "key": {"foo": "foo"}
    }
    assert jsonable_encoder({"key": [model]}, exclude_defaults=True) == {
        "key": [{"foo": "foo"}]
    }
    assert jsonable_encoder({"key": model}) == {
        "key": {"foo": "foo", "bar": "bar", "bla": "bla"}
    }
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
