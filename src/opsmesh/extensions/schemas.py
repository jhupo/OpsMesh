"""Frozen installation, instance and invocation API contracts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class InstallRequest(FrozenValue):
    operation_id: str = Field(min_length=1, max_length=200)
    version_id: UUID
    permission_ceiling: list[str] = Field(default_factory=list, max_length=128)


class RevisionRequest(FrozenValue):
    expected_revision: int = Field(ge=0)


class InstallationView(FrozenValue):
    id: UUID
    version_id: UUID
    namespace: str
    status: str
    revision: int
    artifact: str | None
    permission_ceiling: list[str]
    error_code: str | None


class InstanceConfiguration(FrozenValue):
    installation_id: UUID
    environment_id: UUID | None = None
    configuration_id: UUID | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list, max_length=128)
    model_connections: list[str] = Field(default_factory=list, max_length=64)
    state_limit_bytes: int = Field(default=10485760, ge=0, le=1073741824)

    @model_validator(mode="after")
    def valid_scope(self) -> "InstanceConfiguration":
        for reference in self.model_connections:
            connection, model = reference.split(":", 1)
            UUID(connection)
            if not model.strip() or len(model) > 200:
                raise ValueError("A model reference requires a bounded model name")
        if len(set(self.model_connections)) != len(self.model_connections):
            raise ValueError("Model connection references must be unique")
        return self


class InstanceUpdate(InstanceConfiguration):
    expected_revision: int = Field(ge=0)


class InstanceView(InstanceConfiguration):
    id: UUID
    owner_id: UUID
    status: str
    revision: int


class InvokeRequest(FrozenValue):
    export_id: str = Field(min_length=1, max_length=100)
    operation_id: str = Field(min_length=1, max_length=240)
    arguments: dict[str, Any] = Field(default_factory=dict)


class HookInvocationContext(FrozenValue):
    run_id: UUID
    binding_id: str = Field(min_length=1, max_length=128)
    max_invocations: int = Field(ge=1, le=1024)
    timeout_seconds: int = Field(ge=1, le=120)
    model_tokens: int = Field(ge=0, le=262144)


class InvocationView(FrozenValue):
    id: UUID
    instance_id: UUID
    installation_id: UUID
    namespace_id: UUID
    export_id: str
    status: str
    revision: int
    effects: list[str]
    result: Any = None
    error_code: str | None
    cancel_requested: bool


class ExtensionReconcileRequest(RevisionRequest):
    outcome: Literal["completed", "not_executed", "failed"]
    evidence: dict[str, Any] = Field(min_length=1)
    result: dict[str, Any] = Field(default_factory=dict)


class HostCallView(FrozenValue):
    id: UUID
    operation_id: str
    status: str
    revision: int


class DeploymentRequest(RevisionRequest):
    operation_id: str = Field(min_length=1, max_length=200)


class DeploymentView(FrozenValue):
    id: UUID
    instance_id: UUID
    instance_revision: int
    status: str
    revision: int
    error_code: str | None


ExtensionAction = Literal["install", "invoke", "uninstall", "start_worker", "stop_worker"]


class ExtensionWork(FrozenValue):
    execution_id: UUID
    workflow_id: str
    workflow_name: str
    action: ExtensionAction
    target_id: UUID


def extension_work(
    target_id: UUID, action: ExtensionAction, *, execution_id: UUID | None = None
) -> ExtensionWork:
    return ExtensionWork(
        execution_id=execution_id or target_id,
        workflow_id=f"extension-{action}-{target_id}",
        workflow_name="ExtensionWorkflow",
        action=action,
        target_id=target_id,
    )


class StateMutation(FrozenValue):
    key: str = Field(min_length=1, max_length=512, pattern="^[^\u0000]+$")
    value: Any = None
    expected_version: int | None = Field(default=None, ge=0)
    delete: bool = False


class StateBatch(FrozenValue):
    entries: list[StateMutation] = Field(min_length=1, max_length=256)
    event_drafts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class CleanupRequest(RevisionRequest):
    preview_digest: str = Field(pattern="^[a-f0-9]{64}$")


class StatePage(FrozenValue):
    entries: list[dict[str, Any]]
    next_cursor: str | None


class CleanupPreview(FrozenValue):
    namespace_id: UUID
    revision: int
    used_bytes: int
    entry_count: int
    entries: list[dict[str, Any]]
    preview_digest: str


class CleanupResult(FrozenValue):
    cleaned: bool


class StateSnapshot(FrozenValue):
    installation_id: UUID
    version_id: UUID
    revision: int = Field(ge=0)
    entries: list[dict[str, Any]] = Field(max_length=10_000)

    @model_validator(mode="after")
    def bounded_snapshot(self) -> "StateSnapshot":
        from opsmesh.shared.serialization import canonical_json

        if len(canonical_json(self.model_dump(mode="json")).encode()) > 16 * 1_048_576:
            raise ValueError("The namespace snapshot exceeds its byte bound")
        keys = set()
        for entry in self.entries:
            value = entry.get("value")
            if (
                set(entry) != {"key", "value"}
                or not isinstance(value, dict)
                or set(value) != {"deleted", "value"}
                or not isinstance(value["deleted"], bool)
            ):
                raise ValueError("Invalid namespace snapshot entry")
            mutation = StateMutation(
                key=entry["key"], value=value["value"], delete=value["deleted"]
            )
            if mutation.key in keys:
                raise ValueError("A namespace snapshot cannot repeat a key")
            keys.add(mutation.key)
        return self


class StateRestoreRequest(RevisionRequest):
    snapshot: StateSnapshot


class StateRestoreResult(FrozenValue):
    restored_entries: int
