"""Local explicit runner for declared contracts.

The runner starts a declared export in-process for author testing only. It is
never a production entrypoint: production invocation goes through the server
and an approved isolation environment.
"""

from __future__ import annotations

from typing import Final

from pydantic import JsonValue

from opsmesh_sdk.extensions.operations import Operation
from opsmesh_sdk.extensions.tools import tool_schema_for_operation

MAX_RUN_SECONDS: Final = 60.0


class ContractRunner:
    """Runs declared operations locally against contract fixtures."""

    def __init__(self, operations: dict[str, Operation]) -> None:
        self._operations = operations

    @property
    def export_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))

    def tool_schemas(self) -> dict[str, JsonValue]:
        return {
            export_id: tool_schema_for_operation(operation).model_dump(mode="json")
            for export_id, operation in self._operations.items()
        }

    async def run(self, export_id: str, arguments: dict[str, JsonValue]) -> JsonValue:
        operation = self._operations.get(export_id)
        if operation is None:
            raise KeyError(f"no declared operation {export_id!r}")
        result = await operation.invoke(arguments)
        if result is None or isinstance(result, dict | list | str | int | float | bool):
            return result
        return str(result)


__all__ = ["MAX_RUN_SECONDS", "ContractRunner"]
