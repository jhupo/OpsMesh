"""Operation declarations: the stable export contract for package functions.

An operation is a typed function with declared effects. Declaring one never
runs it; the server invokes approved exports in an isolated environment and
records an Invocation receipt per occurrence.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

from opsmesh_sdk.errors import CancelledError, UnknownEffectError
from opsmesh_sdk.manifest import EffectKind, JsonSchema, OperationExport
from opsmesh_sdk.values import IdempotencyKey

MAX_RESULT_BYTES: Final = 1_048_576

_T = TypeVar("_T")


class Operation:
    """A declared operation: identity, schemas, effects and the typed handler."""

    def __init__(self, export: OperationExport, handler: Callable[..., Awaitable[object]]) -> None:
        self._export = export
        self._handler = handler

    @property
    def export(self) -> OperationExport:
        return self._export

    @property
    def export_id(self) -> str:
        return self._export.export_id

    def describe(self) -> dict[str, JsonValue]:
        """The declaration the server freezes at installation time."""
        return self._export.model_dump(mode="json")

    async def invoke(self, arguments: dict[str, JsonValue]) -> object:
        """Run the declared handler after validating arguments against the schema."""
        self._export.input_schema.validate_instance(arguments)
        return await self._handler(arguments)


class OperationResult(BaseModel):
    """The outcome envelope a declared operation returns to the platform."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: JsonValue = None
    effects: tuple[EffectKind, ...] = ()
    idempotency_key: IdempotencyKey | None = None
    needs_attention: bool = False

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        if len(repr(self.value).encode()) > MAX_RESULT_BYTES:
            raise UnknownEffectError(
                "Operation results must stay within the declared size bound",
                details={"export_effects": [effect.value for effect in self.effects]},
            )
        if self.needs_attention and EffectKind.NONE in self.effects:
            raise ValueError("A needs_attention result must declare its external effects")


def declare_operation(
    export_id: str,
    *,
    description: str,
    input_schema: dict[str, JsonValue] | None = None,
    output_schema: dict[str, JsonValue] | None = None,
    effects: tuple[EffectKind, ...] = (),
    timeout_seconds: int = 60,
    permissions: tuple[str, ...] = (),
) -> Callable[[Callable[..., Awaitable[object]]], Operation]:
    """Declare an operation export from a typed async handler."""
    export = OperationExport(
        export_id=export_id,
        description=description,
        input_schema=_schema_or_default(input_schema),
        output_schema=_schema_or_default(output_schema),
        effects=effects,
        timeout_seconds=timeout_seconds,
        permissions=permissions,
    )

    def decorate(handler: Callable[..., Awaitable[object]]) -> Operation:
        return Operation(export, handler)

    return decorate


def _schema_or_default(schema: dict[str, JsonValue] | None) -> JsonSchema:
    if schema is None:
        return JsonSchema()
    return JsonSchema.model_validate(schema)


def cancelled_outcome(reason: str) -> OperationResult:
    """A cancelled occurrence carries no value and no confirmed effect."""
    del reason
    raise CancelledError("The invocation was cancelled before producing a result")


__all__ = [
    "MAX_RESULT_BYTES",
    "Operation",
    "OperationResult",
    "cancelled_outcome",
    "declare_operation",
]
