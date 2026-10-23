"""Execution identity supplied by the caller, independent of any orchestrator."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from opsmesh_sdk.identity import InvocationSource, Principal
from pydantic import TypeAdapter


@dataclass(frozen=True)
class ExecutionContext:
    run_id: UUID
    user_id: UUID
    instance_id: UUID
    attempt_id: str
    principal: Principal | None = field(default=None, compare=False)
    source: InvocationSource = field(default_factory=InvocationSource)

    def operation_id(self, kind: str, occurrence: str) -> str:
        return f"{self.run_id}:{self.attempt_id}:{kind}:{occurrence}"


def serialize_execution_context(value: object) -> dict[str, Any]:
    if not isinstance(value, ExecutionContext):
        raise TypeError("Expected a trusted execution context")
    return dict(TypeAdapter(ExecutionContext).dump_python(value, mode="json"))
