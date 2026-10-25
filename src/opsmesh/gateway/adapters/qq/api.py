"""Fixed native operation boundary over the official botpy ``BotAPI``."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic_core import to_jsonable_python

from opsmesh.gateway.adapters.errors import OperationRejected
from opsmesh.gateway.adapters.schemas import Operation

_API_METHODS = {
    "qq.message.post": "post_message",
    "qq.c2c.post": "post_c2c_message",
    "qq.group.post": "post_group_message",
    "qq.c2c.file": "post_c2c_file",
    "qq.group.file": "post_group_file",
}


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Operation]:
    operations: dict[str, Operation] = {}
    for operation, method_name in _API_METHODS.items():
        operations[operation] = Operation(
            parameters={"type": "object", "additionalProperties": True},
            result={"type": "object", "additionalProperties": True},
            platform_permissions=f"QQ application permission for botpy.api.BotAPI.{method_name}",
        )
    return operations


async def call(api: Any, operation: str, parameters: dict[str, Any]) -> Any:
    method_name = _API_METHODS.get(operation)
    if method_name is None:
        raise OperationRejected("unknown_qq_operation")
    method = getattr(api, method_name, None)
    if method is None:
        raise OperationRejected("qq_sdk_operation_unavailable")
    try:
        value = await method(**parameters)
    except TypeError as exc:
        raise OperationRejected("invalid_qq_operation_parameters") from exc
    return to_jsonable_python(value)
