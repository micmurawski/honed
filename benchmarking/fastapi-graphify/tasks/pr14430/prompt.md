Fix optional sequence handling with new union syntax from Python 3.10.

For example, this test should pass once it's done:

```python
from typing import Any, Dict, List, Union

from fastapi import FastAPI, UploadFile

from fastapi._compat import (
    Undefined,
    _get_model_config,
    get_cached_model_fields,
    is_scalar_field,
    is_uploadfile_sequence_annotation,
    may_v1,
)

from fastapi._compat.shared import is_bytes_sequence_annotation

from fastapi.testclient import TestClient

from pydantic import BaseModel, ConfigDict

from pydantic.fields import FieldInfo

from .utils import needs_py310, needs_py_lt_314, needs_pydanticv2

@needs_pydanticv2
@needs_py310
def test_serialize_sequence_value_with_optional_list_pipe_union():
    """Test that serialize_sequence_value handles optional lists correctly (with new syntax)."""
    from fastapi._compat import v2

    field_info = FieldInfo(annotation=list[str] | None)
    field = v2.ModelField(name="items", field_info=field_info)
    result = v2.serialize_sequence_value(field=field, value=["a", "b", "c"])
    assert result == ["a", "b", "c"]
    assert isinstance(result, list)
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
