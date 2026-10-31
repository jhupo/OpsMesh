"""Convert SDK values to JSON-safe platform data."""

import dataclasses
from typing import Any


def serialize_sdk_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: serialize_sdk_value(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): serialize_sdk_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serialize_sdk_value(item) for item in value]
    return value
