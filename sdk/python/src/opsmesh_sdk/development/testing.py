"""Contract fixtures and a recordable host stand-in for author tests.

The stand-in only proves SDK contracts. PostgreSQL transactions, host
permissions and Temporal recovery must be verified in real integration.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import JsonValue

from opsmesh_sdk.context import InvocationContext, InvocationSubject
from opsmesh_sdk.extensions.hooks import HookInput
from opsmesh_sdk.extensions.operations import Operation, OperationResult
from opsmesh_sdk.extensions.tools import tool_schema_for_operation
from opsmesh_sdk.manifest import HookStage
from opsmesh_sdk.values import FlowRunRef, ScopeRef

FIXTURE_INSTALLATION_ID: Final = "00000000-0000-0000-0000-000000000001"
FIXTURE_INSTANCE_ID: Final = "00000000-0000-0000-0000-000000000002"
FIXTURE_INVOCATION_ID: Final = "00000000-0000-0000-0000-000000000003"


class RecordingHostClient:
    """A host stand-in that records calls instead of reaching a server."""

    def __init__(self) -> None:
        self.state: dict[str, JsonValue] = {}
        self.published_events: list[dict[str, JsonValue]] = []
        self.submitted_jobs: list[dict[str, JsonValue]] = []
        self.observability_records: list[dict[str, JsonValue]] = []

    def record_state(self, key: str, value: JsonValue) -> None:
        self.state[key] = value

    def record_event(self, draft: dict[str, JsonValue]) -> None:
        self.published_events.append(draft)

    def record_job(self, submission: dict[str, JsonValue]) -> FlowRunRef:
        self.submitted_jobs.append(submission)
        return FlowRunRef(
            flow_run_id=f"fixture-flow-{len(self.submitted_jobs)}",
            definition_revision=str(submission.get("definition_revision", "fixture")),
        )

    def record_observability(self, record: dict[str, JsonValue]) -> None:
        self.observability_records.append(record)


class ContractFixtures:
    """Stable inputs for contract tests: envelopes, hook inputs and results."""

    @staticmethod
    def event_envelope(
        *,
        source_type: str = "run",
        source_id: str = FIXTURE_INVOCATION_ID,
        event_id: str = "fixture-event-1",
        event_version: str = "1",
        scope: ScopeRef | None = None,
    ) -> dict[str, JsonValue]:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "event_id": event_id,
            "event_version": event_version,
            "scope": (scope or ScopeRef()).model_dump(mode="json"),
            "summary": {"fixture": True},
        }

    @staticmethod
    def operation_result(value: JsonValue = None) -> OperationResult:
        return OperationResult(value=value, effects=())


class ContractRunner:
    """Run explicit local operation fixtures; no production permissions are implied."""

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
        return result.value


def hook_input(
    stage: HookStage,
    *,
    binding_id: str = "fixture-binding",
    occurrence: int = 0,
    payload: dict[str, JsonValue] | None = None,
) -> HookInput:
    """A typed hook input for one stage occurrence."""
    return HookInput(
        stage=stage,
        binding_id=binding_id,
        occurrence=occurrence,
        payload=payload or {},
    )


def invocation_context(
    *,
    namespace: str = "fixture.package",
    scope: ScopeRef | None = None,
    deadline_seconds: float | None = 30.0,
) -> InvocationContext:
    """A fixture invocation context with verified fixture identities."""
    return InvocationContext(
        InvocationSubject(
            installation_id=FIXTURE_INSTALLATION_ID,
            instance_id=FIXTURE_INSTANCE_ID,
            invocation_id=FIXTURE_INVOCATION_ID,
            namespace=namespace,
        ),
        scope=scope,
        deadline_seconds=deadline_seconds,
    )


def fixture_ids() -> tuple[UUID, UUID, UUID]:
    return (
        UUID(FIXTURE_INSTALLATION_ID),
        UUID(FIXTURE_INSTANCE_ID),
        UUID(FIXTURE_INVOCATION_ID),
    )


__all__ = [
    "FIXTURE_INSTALLATION_ID",
    "FIXTURE_INSTANCE_ID",
    "FIXTURE_INVOCATION_ID",
    "ContractFixtures",
    "ContractRunner",
    "RecordingHostClient",
    "fixture_ids",
    "hook_input",
    "invocation_context",
]
