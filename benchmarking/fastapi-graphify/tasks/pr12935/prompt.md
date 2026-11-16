Fix `TypeError` when encoding a decimal with a `NaN` or `Infinity` value.

For example, this test should pass once it's done:

```python
from collections import deque

from dataclasses import dataclass

from datetime import datetime, timezone

from decimal import Decimal

from enum import Enum

from math import isinf, isnan

from pathlib import PurePath, PurePosixPath, PureWindowsPath

from typing import Optional

import pytest

from fastapi._compat import PYDANTIC_V2, Undefined

from fastapi.encoders import jsonable_encoder

from pydantic import BaseModel, Field, ValidationError

from .utils import needs_pydanticv1, needs_pydanticv2

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
    role: Optional[RoleEnum] = None

    if PYDANTIC_V2:
        model_config = {"use_enum_values": True}
    else:

        class Config:
            use_enum_values = True

class ModelWithAlias(BaseModel):
    foo: str = Field(alias="Foo")

class ModelWithDefault(BaseModel):
    foo: str = ...  # type: ignore
    bar: str = "bar"
    bla: str = "bla"

@needs_pydanticv2
def test_decimal_encoder_nan():
    data = {"value": Decimal("NaN")}
    assert isnan(jsonable_encoder(data)["value"])
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
