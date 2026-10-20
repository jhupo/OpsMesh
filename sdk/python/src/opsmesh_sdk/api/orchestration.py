"""Generated orchestration API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``orchestration.capability_bindings.list``
- ``orchestration.capability_bindings.create``
- ``orchestration.capability_bindings.get``
- ``orchestration.capability_bindings.update``
- ``orchestration.capability_bindings.connect``
- ``orchestration.configurations.list``
- ``orchestration.configurations.create``
- ``orchestration.configurations.invitations.list``
- ``orchestration.configurations.invitations.accept``
- ``orchestration.configurations.get``
- ``orchestration.configurations.update``
- ``orchestration.configurations.get_closure``
- ``orchestration.configurations.close``
- ``orchestration.configurations.files.list``
- ``orchestration.configurations.files.create``
- ``orchestration.configurations.files.write``
- ``orchestration.configurations.files.content``
- ``orchestration.configurations.invitations.create``
- ``orchestration.configurations.members.delete``
- ``orchestration.configurations.revisions.list``
- ``orchestration.configurations.runs.create``
- ``orchestration.configurations.runs.get``
- ``orchestration.configurations.runs.cancel``
- ``orchestration.configurations.runs.events``
- ``orchestration.configurations.sessions.list``
- ``orchestration.configurations.sessions.create``
- ``orchestration.configurations.sessions.history``
- ``orchestration.definitions.list``
- ``orchestration.definitions.create``
- ``orchestration.definitions.authoring_contract``
- ``orchestration.definitions.get``
- ``orchestration.definitions.update``
- ``orchestration.definitions.archive``
- ``orchestration.definitions.grants.put``
- ``orchestration.definitions.grants.delete``
- ``orchestration.definitions.publish``
- ``orchestration.definitions.revisions.list``
- ``orchestration.definitions.revisions.get``
- ``orchestration.definitions.validate``
- ``orchestration.environments.create``
- ``orchestration.environments.get``
- ``orchestration.environments.close``
- ``orchestration.environments.commands.execute``
- ``orchestration.environments.files.download``
- ``orchestration.environments.files.upload``
- ``orchestration.environments.operations.get``
- ``orchestration.environments.operations.cancel``
- ``orchestration.environments.operations.reconcile``
- ``orchestration.environments.resume``
- ``orchestration.flows.list``
- ``orchestration.flows.create``
- ``orchestration.flows.get``
- ``orchestration.flows.control``
- ``orchestration.flows.events.list``
- ``orchestration.flows.plans.review``
- ``orchestration.flows.plans.apply``
- ``orchestration.flows.signals.submit``
- ``orchestration.flows.steps.list``
- ``orchestration.mcp_connections.list``
- ``orchestration.mcp_connections.create``
- ``orchestration.mcp_connections.get``
- ``orchestration.mcp_connections.close``
- ``orchestration.mcp_connections.connect``
- ``orchestration.mcp_connections.operations.get``
- ``orchestration.mcp_connections.operations.cancel``
- ``orchestration.mcp_connections.operations.reconcile``
- ``orchestration.mcp_connections.tools.list``
- ``orchestration.model_operations.create``
- ``orchestration.model_operations.get``
- ``orchestration.model_operations.reconcile``
- ``orchestration.runs.list``
- ``orchestration.runs.create``
- ``orchestration.runs.get``
- ``orchestration.runs.cancel``
- ``orchestration.runs.events.list``
- ``orchestration.runs.events.stream``
- ``orchestration.runs.operations``
- ``orchestration.runs.reconcile``
- ``orchestration.runs.resume``
- ``orchestration.sessions.create``
- ``orchestration.sessions.delete``
- ``orchestration.sessions.get``
- ``orchestration.sessions.close``
- ``orchestration.sessions.history.list``
- ``orchestration.sessions.history.clear``
- ``orchestration.sessions.history.append``
- ``orchestration.sessions.history.pop``
- ``orchestration.triggers.list``
- ``orchestration.triggers.create``
- ``orchestration.triggers.get``
- ``orchestration.triggers.control``
- ``orchestration.triggers.webhook``
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
    decode_binary,
)


class AgentTemplate(BaseModel):
    agent_tools: list[str] | None = None
    handoffs: list[str] | None = None
    instructions: str = ''
    mcp: list[str] | None = None
    model_slot: str = 'default'
    name: str
    output_schema: dict[str, object] | None = None
    skills: list[str] | None = None
    tools: list[str] | None = None


class AssetRef(BaseModel):
    """One content-addressed input asset of a run.

The destination is a safe relative path inside the execution
environment: the shared path validator rejects absolute paths, parent
segments and backslashes, and the transfer resolves containment
against the environment root before writing."""

    destination: str
    digest: str
    ref: str


class BindingConfiguration(BaseModel):
    export_id: str | None = None
    instance_id: UUID | None = None
    mcp_connection_id: UUID | None = None
    parameters: dict[str, object] | None = None


class BindingConnect(BaseModel):
    configuration_id: UUID | None = None
    environment_id: UUID | None = None
    expected_revision: int
    transport: str = 'streamable_http'


class CancelRequest(BaseModel):
    command_id: UUID
    expected_revision: int


class CapabilitySelection(BaseModel):
    binding_id: UUID
    parameters: dict[str, object] | None = None


class ConfigurationCloseRequest(BaseModel):
    expected_revision: int


class ConfigurationClosureView(BaseModel):
    configuration_id: UUID
    detail: dict[str, object] | None = None
    id: UUID
    status: str


class ConfigurationInvitationCreate(BaseModel):
    directory_access: bool = False
    expires_in_seconds: int = 604800
    permissions: list[str]
    user_id: UUID


class ConfigurationInvitationView(BaseModel):
    configuration_id: UUID
    id: UUID
    status: str


class ConfigurationMembershipView(BaseModel):
    configuration_id: UUID


class ConfigurationRunCreate(BaseModel):
    configuration_revision: int
    input: str | list[dict[str, object]]
    session_id: UUID | None = None


class ConfigurationView(BaseModel):
    definition_revision_id: UUID
    id: UUID
    name: str
    owner_user_id: UUID
    revision: int
    root_directory_id: UUID
    status: str


class DefinitionGrantRequest(BaseModel):
    permissions: list[str]
    user_id: UUID


class EnvironmentCommandRequest(BaseModel):
    command: list[str]
    timeout_seconds: int = 60


class EnvironmentCreateRequest(BaseModel):
    configuration_id: UUID | None = None
    image: str
    root_directory_id: UUID | None = None


class EnvironmentFileDownloadRequest(BaseModel):
    path: str


class EnvironmentFileUploadRequest(BaseModel):
    content_base64: str
    path: str


class EnvironmentView(BaseModel):
    environment_id: UUID
    error_code: str | None
    owner_ref: str
    scope_id: UUID
    status: str


class EventView(BaseModel):
    kind: str
    payload: dict[str, object]
    sequence: int


class ExecutionLimits(BaseModel):
    approval_timeout_seconds: int = 86400
    execution_timeout_seconds: int = 3600
    max_turns: int = 20


class FileCreateRequest(BaseModel):
    directory_id: UUID
    name: str


class FileVersionView(BaseModel):
    created_at: datetime
    created_by_user_id: UUID
    digest: str
    file_id: UUID
    media_type: str | None
    size: int
    version: int


class FileView(BaseModel):
    created_at: datetime
    directory_id: UUID
    file_id: UUID
    latest_version: int
    name: str
    owner_user_id: UUID
    updated_at: datetime


class FlowAuthoringContract(BaseModel):
    definition_schema: dict[str, object]
    mapping_roots: list[str]
    max_nested_depth: int
    max_nodes: int
    node_kinds: list[str]


class FlowCondition(BaseModel):
    left: object
    operator: str
    right: object | None = None


class FlowControl(BaseModel):
    action: str
    command_id: UUID
    expected_revision: int


class FlowEventView(BaseModel):
    kind: str
    sequence: int
    summary: dict[str, object]


class FlowExecutionPolicy(BaseModel):
    """Published effects policy; retries resume receipts without resubmitting children."""

    activity_attempts: int = 3
    cancellation: Literal['confirm_owned'] = 'confirm_owned'
    failure: Literal['cancel_owned'] = 'cancel_owned'
    retry: Literal['resume_receipts'] = 'resume_receipts'


class FlowExport(BaseModel):
    artifact: str
    configuration_revision: int
    export_id: str
    installation_id: UUID
    instance_id: UUID


class FlowLimits(BaseModel):
    max_concurrency: int = 8
    max_depth: int = 8
    max_steps: int = 256
    model_tokens: int = 1000000
    timeout_seconds: int = 3600


class FlowPlanReviewView(BaseModel):
    approval_id: UUID
    status: str


class FlowPublishRequest(BaseModel):
    expected_revision: int


class FlowRunCreate(BaseModel):
    configuration_id: UUID | None = None
    input: dict[str, object] | None = None
    revision_id: UUID


class FlowRunView(BaseModel):
    configuration_id: UUID | None
    error_code: str | None
    id: UUID
    initiated_by_id: UUID
    output: object | None = None
    parent_id: UUID | None
    revision: int
    revision_id: UUID | None
    root_id: UUID
    status: str


class FlowSignal(BaseModel):
    command_id: UUID
    generation: int
    name: str
    step_path: str
    value: object | None = None


class FlowStepView(BaseModel):
    child_id: UUID | None
    child_kind: str | None
    error_code: str | None
    generation: int
    path: str
    status: str


class FlowValidationResult(BaseModel):
    revision: int
    valid: bool


class HistoryAppendRequest(BaseModel):
    """The items one append stores, in order."""

    items: list[dict[str, object]]


class HistoryAppendView(BaseModel):
    """How many items the append stored."""

    appended: int


class HistoryItemView(BaseModel):
    """The item one pop removed, or nothing when the history was empty."""

    item: dict[str, object] | None


class HistoryPage(BaseModel):
    """One bounded read of the native history, oldest first."""

    items: list[dict[str, object]]


class HookBudget(BaseModel):
    max_invocations: int = 128
    model_tokens: int = 0
    timeout_seconds: int = 30


class InstalledExport(BaseModel):
    artifact: str
    configuration_revision: int
    export_id: str
    installation_id: UUID
    instance_id: UUID


class MCPBinding(BaseModel):
    allowed_tools: list[str]
    connection_id: UUID
    tools_digest: str


class MCPCreateRequest(BaseModel):
    command: list[str] | None = None
    configuration_id: UUID | None = None
    endpoint: str | None = None
    environment: dict[str, str] | None = None
    environment_id: UUID | None = None
    name: str
    transport: str


class MCPToolsView(BaseModel):
    connection_id: UUID
    tools: list[dict[str, object]]
    tools_digest: str


class MCPView(BaseModel):
    connection_id: UUID
    error_code: str | None
    name: str
    status: str
    tools: list[dict[str, object]]
    tools_digest: str = ''
    transport: str


class ModelConfiguration(BaseModel):
    connection_id: UUID
    max_output_tokens: int = 4096
    name: str
    reasoning_effort: str | None = None
    temperature: float | None = None


class ModelOperationRequest(BaseModel):
    configuration_id: UUID | None = None
    connection_id: UUID
    inputs: list[str]
    kind: str
    max_output_tokens: int = 4096
    model: str
    reasoning_effort: str | None = None
    source_ref: UUID
    temperature: float | None = None


class ModelOperationView(BaseModel):
    id: UUID
    result: dict[str, object] | None = None
    revision: int
    status: str
    usage: dict[str, object]


class NormalizedUsage(BaseModel):
    """Quantity-only operation usage; input tokens exclude the cached bucket."""

    cached_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_invocations: int = 0


class OperationView(BaseModel):
    accounted: bool
    kind: str
    operation_id: str
    status: str
    usage: dict[str, int]


class ResumeRequest(BaseModel):
    approval_id: UUID
    command_id: UUID
    expected_revision: int


class RunOutput(BaseModel):
    media_type: str | None = None
    name: str
    path: str


class RunView(BaseModel):
    artifacts: list[UUID] | None = None
    created_at: datetime
    error_code: str | None
    output: object | None = None
    revision: int
    run_id: UUID
    sequence: int
    session_id: UUID
    status: str
    usage: dict[str, int] | None = None


class RuntimeControlRequest(BaseModel):
    command_id: UUID
    expected_revision: int


class RuntimeOperationView(BaseModel):
    operation_id: str
    result: dict[str, object] | None = None
    revision: int = 0
    status: str


class RuntimeReconcileRequest(BaseModel):
    command_id: UUID
    confirmed_status: str
    evidence: dict[str, object]
    expected_revision: int
    result: dict[str, object] | None = None
    unresolved_operations: list[str]


class SessionCreateRequest(BaseModel):
    """The scope a new session is opened in; the user scope is the default."""

    configuration_id: UUID | None = None


class SessionView(BaseModel):
    """One model session as stored."""

    configuration_id: UUID | None
    created_at: datetime
    owner_ref: str
    principal_id: UUID
    scope_id: UUID
    session_id: UUID
    status: str
    updated_at: datetime


class TriggerAccepted(BaseModel):
    flow_run_id: UUID
    status: str


class TriggerConfiguration(BaseModel):
    cron: str | None = None
    event_type: str = ''
    input: dict[str, object] | None = None
    instance_id: UUID | None = None
    interval_seconds: int | None = None
    payload_schema: dict[str, object] | None = None
    replay_seconds: int = 300
    source_kind: str = 'extension'
    time_zone: str = 'UTC'


class TriggerControl(BaseModel):
    expected_revision: int
    status: str


class AgentGraphSpec(BaseModel):
    agents: dict[str, AgentTemplate]
    entry_agent: str
    limits: ExecutionLimits | None = None


class BindingCreate(BaseModel):
    configuration: BindingConfiguration | None = None
    name: str
    version_id: UUID


class BindingUpdate(BaseModel):
    configuration: BindingConfiguration | None = None
    expected_revision: int
    status: str = 'enabled'
    version_id: UUID


class BindingView(BaseModel):
    configuration: BindingConfiguration
    id: UUID
    name: str
    owner_user_id: UUID
    revision: int
    status: str
    version_digest: str
    version_id: UUID


class ConfigurationHook(BaseModel):
    binding_id: UUID
    budget: HookBudget | None = None
    order: int = 0
    stages: list[str]


class EventPage(BaseModel):
    events: list[EventView]
    next_cursor: int


class InstalledHook(BaseModel):
    artifact: str
    binding_id: str
    budget: HookBudget | None = None
    configuration_revision: int
    export_id: str
    failure_policy: str = 'block'
    installation_id: UUID
    instance_id: UUID
    order: int = 0
    stages: list[str]


class ModelOperationReconciliation(BaseModel):
    actual_usage: NormalizedUsage | None = None
    evidence: dict[str, object]
    expected_revision: int
    outcome: str
    result: dict[str, object] | None = None


class OperationConfirmation(BaseModel):
    operation_id: str
    outcome: str
    result: dict[str, object] | None = None
    usage: NormalizedUsage | None = None


class TriggerCreate(BaseModel):
    configuration: TriggerConfiguration
    configuration_id: UUID | None = None
    kind: str
    revision_id: UUID
    webhook_secret: str | None = None


class TriggerView(BaseModel):
    configuration: TriggerConfiguration
    configuration_id: UUID | None
    id: UUID
    kind: str
    revision: int
    revision_id: UUID
    status: str


class opsmesh__orchestration__execution__contracts__ToolConfiguration(BaseModel):
    approval_required: bool = True
    command: list[str] | None = None
    description: str = ''
    extension: InstalledExport | None = None
    input_schema: dict[str, object]
    name: str


class AgentConfiguration(BaseModel):
    agent_tools: list[str] | None = None
    handoffs: list[str] | None = None
    instructions: str = ''
    mcp_connections: list[MCPBinding] | None = None
    model: ModelConfiguration
    name: str
    output_schema: dict[str, object] | None = None
    tools: list[opsmesh__orchestration__execution__contracts__ToolConfiguration] | None = None


class ConfigurationSpec(BaseModel):
    assets: list[AssetRef] | None = None
    capabilities: dict[str, CapabilitySelection] | None = None
    environment_id: UUID | None = None
    hooks: list[ConfigurationHook] | None = None
    models: dict[str, ModelConfiguration] | None = None
    outputs: list[RunOutput] | None = None


class ReconcileRequest(BaseModel):
    command_id: UUID
    evidence: dict[str, object]
    expected_revision: int
    operations: list[OperationConfirmation] | None = None


class ConfigurationCreate(BaseModel):
    definition_revision_id: UUID
    name: str
    root_directory_id: UUID | None = None
    specification: ConfigurationSpec | None = None


class ConfigurationRevisionView(BaseModel):
    configuration_id: UUID
    digest: str
    id: UUID
    number: int
    specification: ConfigurationSpec


class ConfigurationUpdate(BaseModel):
    expected_revision: int
    specification: ConfigurationSpec


class ExecutionRequest(BaseModel):
    agents: dict[str, AgentConfiguration]
    assets: list[AssetRef] | None = None
    configuration_source: str | None = None
    entry_agent: str
    environment_id: UUID | None = None
    hooks: list[InstalledHook] | None = None
    input: str | list[dict[str, object]]
    limits: ExecutionLimits | None = None
    outputs: list[RunOutput] | None = None
    parent_invocation_id: UUID | None = None
    session_id: UUID


class DefinitionCreate(BaseModel):
    kind: str = 'flow'
    name: str
    spec: AgentGraphSpec | FlowDefinitionSpecInput
    visibility: str = 'private'


class DefinitionRevisionView(BaseModel):
    definition_id: UUID
    digest: str
    id: UUID
    number: int
    spec: AgentGraphSpec | FlowDefinitionSpecOutput


class DefinitionUpdate(BaseModel):
    expected_revision: int
    name: str
    spec: AgentGraphSpec | FlowDefinitionSpecInput
    visibility: str = 'private'


class DefinitionView(BaseModel):
    draft: AgentGraphSpec | FlowDefinitionSpecOutput | None = None
    id: UUID
    kind: str
    name: str
    owner_id: UUID
    revision: int
    status: str
    visibility: str


class FlowDefinitionSpecInput(BaseModel):
    graph: FlowGraphInput
    input_schema: dict[str, object] | None = None
    limits: FlowLimits | None = None
    output_schema: dict[str, object] | None = None
    policy: FlowExecutionPolicy | None = None
    template_source: str | None = None


class FlowDefinitionSpecOutput(BaseModel):
    graph: FlowGraphOutput
    input_schema: dict[str, object] | None = None
    limits: FlowLimits | None = None
    output_schema: dict[str, object] | None = None
    policy: FlowExecutionPolicy | None = None
    template_source: str | None = None


class FlowGraphInput(BaseModel):
    nodes: list[FlowNodeInput]
    output: object | None = None


class FlowGraphOutput(BaseModel):
    nodes: list[FlowNodeOutput]
    output: object | None = None


class FlowNodeInput(BaseModel):
    allowed_kinds: list[str] | None = None
    allowed_references: list[str] | None = None
    approval_id: UUID | None = None
    approval_required: bool = False
    approver_user_id: UUID | None = None
    arguments: dict[str, object] | None = None
    body: FlowGraphInput | None = None
    cancel_remaining: bool = True
    concurrency: int = 1
    condition: FlowCondition | None = None
    configuration_revision_id: UUID | None = None
    depends_on: list[str] | None = None
    export: FlowExport | None = None
    external_step: str | None = None
    input_schema: dict[str, object] | None = None
    join: str = 'all'
    key: str
    kind: str
    max_expanded_nodes: int = 32
    max_iterations: int = 1
    output_schema: dict[str, object] | None = None
    quorum: int = 1
    revision_id: UUID | None = None
    run_if: FlowCondition | None = None
    signal_name: str | None = None
    wait_kind: str = 'timer'
    wait_seconds: int = 0


class FlowNodeOutput(BaseModel):
    allowed_kinds: list[str] | None = None
    allowed_references: list[str] | None = None
    approval_id: UUID | None = None
    approval_required: bool = False
    approver_user_id: UUID | None = None
    arguments: dict[str, object] | None = None
    body: FlowGraphOutput | None = None
    cancel_remaining: bool = True
    concurrency: int = 1
    condition: FlowCondition | None = None
    configuration_revision_id: UUID | None = None
    depends_on: list[str] | None = None
    export: FlowExport | None = None
    external_step: str | None = None
    input_schema: dict[str, object] | None = None
    join: str = 'all'
    key: str
    kind: str
    max_expanded_nodes: int = 32
    max_iterations: int = 1
    output_schema: dict[str, object] | None = None
    quorum: int = 1
    revision_id: UUID | None = None
    run_if: FlowCondition | None = None
    signal_name: str | None = None
    wait_kind: str = 'timer'
    wait_seconds: int = 0


class FlowPlanProposal(BaseModel):
    approval_id: UUID | None = None
    command_id: UUID
    expected_revision: int
    graph: FlowGraphInput
    source_invocation_id: UUID
    step_path: str


class FlowPlanReview(BaseModel):
    approval_id: UUID | None = None
    approver_user_id: UUID | None = None
    command_id: UUID
    expected_revision: int
    graph: FlowGraphInput
    source_invocation_id: UUID
    step_path: str


AgentTemplate.model_rebuild()
AssetRef.model_rebuild()
BindingConfiguration.model_rebuild()
BindingConnect.model_rebuild()
CancelRequest.model_rebuild()
CapabilitySelection.model_rebuild()
ConfigurationCloseRequest.model_rebuild()
ConfigurationClosureView.model_rebuild()
ConfigurationInvitationCreate.model_rebuild()
ConfigurationInvitationView.model_rebuild()
ConfigurationMembershipView.model_rebuild()
ConfigurationRunCreate.model_rebuild()
ConfigurationView.model_rebuild()
DefinitionGrantRequest.model_rebuild()
EnvironmentCommandRequest.model_rebuild()
EnvironmentCreateRequest.model_rebuild()
EnvironmentFileDownloadRequest.model_rebuild()
EnvironmentFileUploadRequest.model_rebuild()
EnvironmentView.model_rebuild()
EventView.model_rebuild()
ExecutionLimits.model_rebuild()
FileCreateRequest.model_rebuild()
FileVersionView.model_rebuild()
FileView.model_rebuild()
FlowAuthoringContract.model_rebuild()
FlowCondition.model_rebuild()
FlowControl.model_rebuild()
FlowEventView.model_rebuild()
FlowExecutionPolicy.model_rebuild()
FlowExport.model_rebuild()
FlowLimits.model_rebuild()
FlowPlanReviewView.model_rebuild()
FlowPublishRequest.model_rebuild()
FlowRunCreate.model_rebuild()
FlowRunView.model_rebuild()
FlowSignal.model_rebuild()
FlowStepView.model_rebuild()
FlowValidationResult.model_rebuild()
HistoryAppendRequest.model_rebuild()
HistoryAppendView.model_rebuild()
HistoryItemView.model_rebuild()
HistoryPage.model_rebuild()
HookBudget.model_rebuild()
InstalledExport.model_rebuild()
MCPBinding.model_rebuild()
MCPCreateRequest.model_rebuild()
MCPToolsView.model_rebuild()
MCPView.model_rebuild()
ModelConfiguration.model_rebuild()
ModelOperationRequest.model_rebuild()
ModelOperationView.model_rebuild()
NormalizedUsage.model_rebuild()
OperationView.model_rebuild()
ResumeRequest.model_rebuild()
RunOutput.model_rebuild()
RunView.model_rebuild()
RuntimeControlRequest.model_rebuild()
RuntimeOperationView.model_rebuild()
RuntimeReconcileRequest.model_rebuild()
SessionCreateRequest.model_rebuild()
SessionView.model_rebuild()
TriggerAccepted.model_rebuild()
TriggerConfiguration.model_rebuild()
TriggerControl.model_rebuild()
AgentGraphSpec.model_rebuild()
BindingCreate.model_rebuild()
BindingUpdate.model_rebuild()
BindingView.model_rebuild()
ConfigurationHook.model_rebuild()
EventPage.model_rebuild()
InstalledHook.model_rebuild()
ModelOperationReconciliation.model_rebuild()
OperationConfirmation.model_rebuild()
TriggerCreate.model_rebuild()
TriggerView.model_rebuild()
opsmesh__orchestration__execution__contracts__ToolConfiguration.model_rebuild()
AgentConfiguration.model_rebuild()
ConfigurationSpec.model_rebuild()
ReconcileRequest.model_rebuild()
ConfigurationCreate.model_rebuild()
ConfigurationRevisionView.model_rebuild()
ConfigurationUpdate.model_rebuild()
ExecutionRequest.model_rebuild()
DefinitionCreate.model_rebuild()
DefinitionRevisionView.model_rebuild()
DefinitionUpdate.model_rebuild()
DefinitionView.model_rebuild()
FlowDefinitionSpecInput.model_rebuild()
FlowDefinitionSpecOutput.model_rebuild()
FlowGraphInput.model_rebuild()
FlowGraphOutput.model_rebuild()
FlowNodeInput.model_rebuild()
FlowNodeOutput.model_rebuild()
FlowPlanProposal.model_rebuild()
FlowPlanReview.model_rebuild()


class OrchestrationCapabilityBindingsClient:
    """Generated client for the ``orchestration.capability_bindings`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        limit: int = 100,
        after: UUID | None = None,
    ) -> builtins.list[BindingView]:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("after", after),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/capability-bindings",
                query=query,
            ),
            self._channel.context(),
        )
        return [BindingView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: BindingCreate) -> BindingView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/capability-bindings",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return BindingView.model_validate(response.body)

    async def get(self, binding_id: UUID) -> BindingView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/capability-bindings/{binding_id}",
            ),
            self._channel.context(),
        )
        return BindingView.model_validate(response.body)

    async def update(self, binding_id: UUID, request: BindingUpdate) -> BindingView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/capability-bindings/{binding_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return BindingView.model_validate(response.body)

    async def connect(
        self,
        binding_id: UUID,
        request: BindingConnect,
        *,
        idempotency_key: str,
    ) -> BindingView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/capability-bindings/{binding_id}/connection",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return BindingView.model_validate(response.body)


class OrchestrationConfigurationsFilesClient:
    """Generated client for the ``orchestration.configurations.files`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, configuration_id: UUID, *, directory_id: UUID) -> builtins.list[FileView]:
        query = {
            name: str(value)
            for name, value in (
                ("directory_id", directory_id),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/files",
                query=query,
            ),
            self._channel.context(),
        )
        return [FileView.model_validate(item) for item in decode_array(response)]

    async def create(
        self,
        configuration_id: UUID,
        request: FileCreateRequest,
        *,
        idempotency_key: str,
    ) -> FileView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/files",
                query=query,
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return FileView.model_validate(response.body)

    async def write(
        self,
        configuration_id: UUID,
        file_id: UUID,
        content: bytes,
        *,
        expected_version: int,
        idempotency_key: str,
        content_type: str,
    ) -> FileVersionView:
        query = {
            name: str(value)
            for name, value in (
                ("expected_version", expected_version),
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/files/{file_id}/content",
                query=query,
                headers={"content-type": content_type},
                content=content,
            ),
            self._channel.context(),
        )
        return FileVersionView.model_validate(response.body)

    async def content(self, configuration_id: UUID, file_id: UUID, number: int) -> bytes:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/files/{file_id}/versions/{number}/content",
            ),
            self._channel.context(),
        )
        return decode_binary(response)


class OrchestrationConfigurationsInvitationsClient:
    """Generated client for the ``orchestration.configurations.invitations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self) -> builtins.list[ConfigurationInvitationView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/invitations/pending",
            ),
            self._channel.context(),
        )
        return [ConfigurationInvitationView.model_validate(item) for item in decode_array(response)]

    async def accept(self, invitation_id: UUID) -> ConfigurationMembershipView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/invitations/{invitation_id}/accept",
            ),
            self._channel.context(),
        )
        return ConfigurationMembershipView.model_validate(response.body)

    async def create(
        self,
        configuration_id: UUID,
        request: ConfigurationInvitationCreate,
    ) -> ConfigurationInvitationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/invitations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ConfigurationInvitationView.model_validate(response.body)


class OrchestrationConfigurationsMembersClient:
    """Generated client for the ``orchestration.configurations.members`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def delete(self, configuration_id: UUID, user_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/members/{user_id}",
            ),
            self._channel.context(),
        )


class OrchestrationConfigurationsRevisionsClient:
    """Generated client for the ``orchestration.configurations.revisions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        configuration_id: UUID,
        *,
        after_number: int = 0,
        limit: int = 100,
    ) -> builtins.list[ConfigurationRevisionView]:
        query = {
            name: str(value)
            for name, value in (
                ("after_number", after_number),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/revisions",
                query=query,
            ),
            self._channel.context(),
        )
        return [ConfigurationRevisionView.model_validate(item) for item in decode_array(response)]


class OrchestrationConfigurationsRunsClient:
    """Generated client for the ``orchestration.configurations.runs`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def create(
        self,
        configuration_id: UUID,
        request: ConfigurationRunCreate,
        *,
        idempotency_key: str,
    ) -> RunView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/runs",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def get(self, configuration_id: UUID, run_id: UUID) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/runs/{run_id}",
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def cancel(self, configuration_id: UUID, run_id: UUID, request: CancelRequest) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/runs/{run_id}/cancel",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def events(
        self,
        configuration_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> EventPage:
        query = {
            name: str(value)
            for name, value in (
                ("after_sequence", after_sequence),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/runs/{run_id}/events",
                query=query,
            ),
            self._channel.context(),
        )
        return EventPage.model_validate(response.body)


class OrchestrationConfigurationsSessionsClient:
    """Generated client for the ``orchestration.configurations.sessions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        configuration_id: UUID,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[SessionView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/sessions",
                query=query,
            ),
            self._channel.context(),
        )
        return [SessionView.model_validate(item) for item in decode_array(response)]

    async def create(self, configuration_id: UUID) -> SessionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/sessions",
            ),
            self._channel.context(),
        )
        return SessionView.model_validate(response.body)

    async def history(
        self,
        configuration_id: UUID,
        session_id: UUID,
        *,
        limit: int = 100,
    ) -> HistoryPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/sessions/{session_id}/history",
                query=query,
            ),
            self._channel.context(),
        )
        return HistoryPage.model_validate(response.body)


class OrchestrationConfigurationsClient:
    """Generated client for the ``orchestration.configurations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.files = OrchestrationConfigurationsFilesClient(channel)
        self.invitations = OrchestrationConfigurationsInvitationsClient(channel)
        self.members = OrchestrationConfigurationsMembersClient(channel)
        self.revisions = OrchestrationConfigurationsRevisionsClient(channel)
        self.runs = OrchestrationConfigurationsRunsClient(channel)
        self.sessions = OrchestrationConfigurationsSessionsClient(channel)

    async def list(
        self,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[ConfigurationView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations",
                query=query,
            ),
            self._channel.context(),
        )
        return [ConfigurationView.model_validate(item) for item in decode_array(response)]

    async def create(
        self,
        request: ConfigurationCreate,
        *,
        idempotency_key: str,
    ) -> ConfigurationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ConfigurationView.model_validate(response.body)

    async def get(self, configuration_id: UUID) -> ConfigurationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}",
            ),
            self._channel.context(),
        )
        return ConfigurationView.model_validate(response.body)

    async def update(
        self,
        configuration_id: UUID,
        request: ConfigurationUpdate,
    ) -> ConfigurationRevisionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ConfigurationRevisionView.model_validate(response.body)

    async def get_closure(self, configuration_id: UUID) -> ConfigurationClosureView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/close",
            ),
            self._channel.context(),
        )
        return ConfigurationClosureView.model_validate(response.body)

    async def close(
        self,
        configuration_id: UUID,
        request: ConfigurationCloseRequest,
    ) -> ConfigurationClosureView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/configurations/{configuration_id}/close",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return ConfigurationClosureView.model_validate(response.body)


class OrchestrationDefinitionsGrantsClient:
    """Generated client for the ``orchestration.definitions.grants`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def put(self, definition_id: UUID, request: DefinitionGrantRequest) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/grants",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )

    async def delete(self, definition_id: UUID, user_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/grants/{user_id}",
            ),
            self._channel.context(),
        )


class OrchestrationDefinitionsRevisionsClient:
    """Generated client for the ``orchestration.definitions.revisions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        definition_id: UUID,
        *,
        after_number: int = 0,
        limit: int = 100,
    ) -> builtins.list[DefinitionRevisionView]:
        query = {
            name: str(value)
            for name, value in (
                ("after_number", after_number),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/revisions",
                query=query,
            ),
            self._channel.context(),
        )
        return [DefinitionRevisionView.model_validate(item) for item in decode_array(response)]

    async def get(self, definition_id: UUID, revision_id: UUID) -> DefinitionRevisionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/revisions/{revision_id}",
            ),
            self._channel.context(),
        )
        return DefinitionRevisionView.model_validate(response.body)


class OrchestrationDefinitionsClient:
    """Generated client for the ``orchestration.definitions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.grants = OrchestrationDefinitionsGrantsClient(channel)
        self.revisions = OrchestrationDefinitionsRevisionsClient(channel)

    async def list(
        self,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[DefinitionView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/definitions",
                query=query,
            ),
            self._channel.context(),
        )
        return [DefinitionView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: DefinitionCreate) -> DefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/definitions",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return DefinitionView.model_validate(response.body)

    async def authoring_contract(self) -> FlowAuthoringContract:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/definitions/authoring-contract",
            ),
            self._channel.context(),
        )
        return FlowAuthoringContract.model_validate(response.body)

    async def get(self, definition_id: UUID) -> DefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}",
            ),
            self._channel.context(),
        )
        return DefinitionView.model_validate(response.body)

    async def update(self, definition_id: UUID, request: DefinitionUpdate) -> DefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return DefinitionView.model_validate(response.body)

    async def archive(self, definition_id: UUID, request: FlowPublishRequest) -> DefinitionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/archive",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return DefinitionView.model_validate(response.body)

    async def publish(
        self,
        definition_id: UUID,
        request: FlowPublishRequest,
    ) -> DefinitionRevisionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/publish",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return DefinitionRevisionView.model_validate(response.body)

    async def validate(self, definition_id: UUID) -> FlowValidationResult:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/definitions/{definition_id}/validate",
            ),
            self._channel.context(),
        )
        return FlowValidationResult.model_validate(response.body)


class OrchestrationEnvironmentsCommandsClient:
    """Generated client for the ``orchestration.environments.commands`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def execute(
        self,
        environment_id: UUID,
        request: EnvironmentCommandRequest,
        *,
        idempotency_key: str,
    ) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/commands",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationEnvironmentsFilesClient:
    """Generated client for the ``orchestration.environments.files`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def download(
        self,
        environment_id: UUID,
        request: EnvironmentFileDownloadRequest,
        *,
        idempotency_key: str,
    ) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/files/download",
                query=query,
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def upload(
        self,
        environment_id: UUID,
        request: EnvironmentFileUploadRequest,
        *,
        idempotency_key: str,
    ) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/files/upload",
                query=query,
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationEnvironmentsOperationsClient:
    """Generated client for the ``orchestration.environments.operations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def get(self, environment_id: UUID, operation_id: str) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/operations/{operation_id}",
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def cancel(
        self,
        environment_id: UUID,
        operation_id: str,
        request: RuntimeControlRequest,
    ) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/operations/{operation_id}/cancel",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def reconcile(
        self,
        environment_id: UUID,
        operation_id: str,
        request: RuntimeReconcileRequest,
    ) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/operations/{operation_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationEnvironmentsClient:
    """Generated client for the ``orchestration.environments`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.commands = OrchestrationEnvironmentsCommandsClient(channel)
        self.files = OrchestrationEnvironmentsFilesClient(channel)
        self.operations = OrchestrationEnvironmentsOperationsClient(channel)

    async def create(
        self,
        request: EnvironmentCreateRequest,
        *,
        idempotency_key: str,
    ) -> EnvironmentView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return EnvironmentView.model_validate(response.body)

    async def get(self, environment_id: UUID) -> EnvironmentView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}",
            ),
            self._channel.context(),
        )
        return EnvironmentView.model_validate(response.body)

    async def close(self, environment_id: UUID, *, idempotency_key: str) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/close",
                query=query,
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def resume(self, environment_id: UUID, *, idempotency_key: str) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/environments/{environment_id}/resume",
                query=query,
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationFlowsEventsClient:
    """Generated client for the ``orchestration.flows.events`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> builtins.list[FlowEventView]:
        query = {
            name: str(value)
            for name, value in (
                ("after_sequence", after_sequence),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/events",
                query=query,
            ),
            self._channel.context(),
        )
        return [FlowEventView.model_validate(item) for item in decode_array(response)]


class OrchestrationFlowsPlansClient:
    """Generated client for the ``orchestration.flows.plans`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def review(self, run_id: UUID, request: FlowPlanReview) -> FlowPlanReviewView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/plan-reviews",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return FlowPlanReviewView.model_validate(response.body)

    async def apply(self, run_id: UUID, request: FlowPlanProposal) -> FlowRunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/plans",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return FlowRunView.model_validate(response.body)


class OrchestrationFlowsSignalsClient:
    """Generated client for the ``orchestration.flows.signals`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def submit(self, run_id: UUID, request: FlowSignal) -> FlowRunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/signals",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return FlowRunView.model_validate(response.body)


class OrchestrationFlowsStepsClient:
    """Generated client for the ``orchestration.flows.steps`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        run_id: UUID,
        *,
        after_path: str = '',
        limit: int = 100,
    ) -> builtins.list[FlowStepView]:
        query = {
            name: str(value)
            for name, value in (
                ("after_path", after_path),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/steps",
                query=query,
            ),
            self._channel.context(),
        )
        return [FlowStepView.model_validate(item) for item in decode_array(response)]


class OrchestrationFlowsClient:
    """Generated client for the ``orchestration.flows`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.events = OrchestrationFlowsEventsClient(channel)
        self.plans = OrchestrationFlowsPlansClient(channel)
        self.signals = OrchestrationFlowsSignalsClient(channel)
        self.steps = OrchestrationFlowsStepsClient(channel)

    async def list(
        self,
        *,
        configuration_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[FlowRunView]:
        query = {
            name: str(value)
            for name, value in (
                ("configuration_id", configuration_id),
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/flows",
                query=query,
            ),
            self._channel.context(),
        )
        return [FlowRunView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: FlowRunCreate, *, idempotency_key: str) -> FlowRunView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/flows",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return FlowRunView.model_validate(response.body)

    async def get(self, run_id: UUID) -> FlowRunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}",
            ),
            self._channel.context(),
        )
        return FlowRunView.model_validate(response.body)

    async def control(self, run_id: UUID, request: FlowControl) -> FlowRunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/flows/{run_id}/control",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return FlowRunView.model_validate(response.body)


class OrchestrationMcpConnectionsOperationsClient:
    """Generated client for the ``orchestration.mcp_connections.operations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def get(self, connection_id: UUID, operation_id: str) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/operations/{operation_id}",
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def cancel(
        self,
        connection_id: UUID,
        operation_id: str,
        request: RuntimeControlRequest,
    ) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/operations/{operation_id}/cancel",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def reconcile(
        self,
        connection_id: UUID,
        operation_id: str,
        request: RuntimeReconcileRequest,
    ) -> RuntimeOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/operations/{operation_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationMcpConnectionsToolsClient:
    """Generated client for the ``orchestration.mcp_connections.tools`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, connection_id: UUID) -> MCPToolsView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/tools",
            ),
            self._channel.context(),
        )
        return MCPToolsView.model_validate(response.body)


class OrchestrationMcpConnectionsClient:
    """Generated client for the ``orchestration.mcp_connections`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.operations = OrchestrationMcpConnectionsOperationsClient(channel)
        self.tools = OrchestrationMcpConnectionsToolsClient(channel)

    async def list(
        self,
        *,
        configuration_id: UUID | None = None,
        after: UUID | None = None,
        limit: int = 50,
    ) -> builtins.list[MCPView]:
        query = {
            name: str(value)
            for name, value in (
                ("configuration_id", configuration_id),
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/mcp-connections",
                query=query,
            ),
            self._channel.context(),
        )
        return [MCPView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: MCPCreateRequest, *, idempotency_key: str) -> MCPView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/mcp-connections",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return MCPView.model_validate(response.body)

    async def get(self, connection_id: UUID) -> MCPView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}",
            ),
            self._channel.context(),
        )
        return MCPView.model_validate(response.body)

    async def close(self, connection_id: UUID, *, idempotency_key: str) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/close",
                query=query,
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)

    async def connect(self, connection_id: UUID, *, idempotency_key: str) -> RuntimeOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/mcp-connections/{connection_id}/connect",
                query=query,
            ),
            self._channel.context(),
        )
        return RuntimeOperationView.model_validate(response.body)


class OrchestrationModelOperationsClient:
    """Generated client for the ``orchestration.model_operations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def create(
        self,
        request: ModelOperationRequest,
        *,
        idempotency_key: str,
    ) -> ModelOperationView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/model-operations",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelOperationView.model_validate(response.body)

    async def get(self, operation_id: UUID) -> ModelOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/model-operations/{operation_id}",
            ),
            self._channel.context(),
        )
        return ModelOperationView.model_validate(response.body)

    async def reconcile(
        self,
        operation_id: UUID,
        request: ModelOperationReconciliation,
    ) -> ModelOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/model-operations/{operation_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelOperationView.model_validate(response.body)


class OrchestrationRunsEventsClient:
    """Generated client for the ``orchestration.runs.events`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, run_id: UUID, *, after: int = 0, limit: int = 100) -> EventPage:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/events",
                query=query,
            ),
            self._channel.context(),
        )
        return EventPage.model_validate(response.body)

    async def stream(self, run_id: UUID, *, after: int = 0) -> AsyncIterator[EventView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
            )
            if value is not None
        }
        async for chunk in self._channel.transport.stream(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/events/stream",
                query=query,
                stream=True,
            ),
            self._channel.context(),
        ):
            yield EventView.model_validate(chunk.data)


class OrchestrationRunsClient:
    """Generated client for the ``orchestration.runs`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.events = OrchestrationRunsEventsClient(channel)

    async def list(
        self,
        *,
        session_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> builtins.list[RunView]:
        query = {
            name: str(value)
            for name, value in (
                ("session_id", session_id),
                ("after_sequence", after_sequence),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/runs",
                query=query,
            ),
            self._channel.context(),
        )
        return [RunView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: ExecutionRequest, *, idempotency_key: str) -> RunView:
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/runs",
                query=query,
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def get(self, run_id: UUID) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}",
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def cancel(self, run_id: UUID, request: CancelRequest) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/cancel",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def operations(self, run_id: UUID) -> builtins.list[OperationView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/operations",
            ),
            self._channel.context(),
        )
        return [OperationView.model_validate(item) for item in decode_array(response)]

    async def reconcile(self, run_id: UUID, request: ReconcileRequest) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/reconcile",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)

    async def resume(self, run_id: UUID, request: ResumeRequest) -> RunView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/runs/{run_id}/resume",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return RunView.model_validate(response.body)


class OrchestrationSessionsHistoryClient:
    """Generated client for the ``orchestration.sessions.history`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, session_id: UUID, *, limit: int = 100) -> HistoryPage:
        """Read the native history of one visible session, oldest first."""
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}/history",
                query=query,
            ),
            self._channel.context(),
        )
        return HistoryPage.model_validate(response.body)

    async def clear(self, session_id: UUID, *, idempotency_key: str) -> None:
        """Clear the native history of one open session."""
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}/history/clear",
                query=query,
            ),
            self._channel.context(),
        )

    async def append(
        self,
        session_id: UUID,
        request: HistoryAppendRequest,
        *,
        idempotency_key: str,
    ) -> HistoryAppendView:
        """Append items to the native history of one open session."""
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}/history/items",
                query=query,
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return HistoryAppendView.model_validate(response.body)

    async def pop(self, session_id: UUID, *, idempotency_key: str) -> HistoryItemView:
        """Remove and return the newest history item of one open session."""
        query = {
            name: str(value)
            for name, value in (
                ("idempotency_key", idempotency_key),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}/history/pop",
                query=query,
            ),
            self._channel.context(),
        )
        return HistoryItemView.model_validate(response.body)


class OrchestrationSessionsClient:
    """Generated client for the ``orchestration.sessions`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.history = OrchestrationSessionsHistoryClient(channel)

    async def create(self, request: SessionCreateRequest) -> SessionView:
        """Open one model session in the requested scope."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/sessions",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return SessionView.model_validate(response.body)

    async def delete(self, session_id: UUID) -> None:
        """Delete one closed model session together with its native history."""
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}",
            ),
            self._channel.context(),
        )

    async def get(self, session_id: UUID) -> SessionView:
        """Read one model session of the current principal."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}",
            ),
            self._channel.context(),
        )
        return SessionView.model_validate(response.body)

    async def close(self, session_id: UUID) -> SessionView:
        """Close one model session, draining behind its active runs."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/sessions/{session_id}/close",
            ),
            self._channel.context(),
        )
        return SessionView.model_validate(response.body)


class OrchestrationTriggersClient:
    """Generated client for the ``orchestration.triggers`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        after: UUID | None = None,
        limit: int = 100,
    ) -> builtins.list[TriggerView]:
        query = {
            name: str(value)
            for name, value in (
                ("after", after),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/triggers",
                query=query,
            ),
            self._channel.context(),
        )
        return [TriggerView.model_validate(item) for item in decode_array(response)]

    async def create(self, request: TriggerCreate) -> TriggerView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/triggers",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return TriggerView.model_validate(response.body)

    async def get(self, trigger_id: UUID) -> TriggerView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/orchestration/triggers/{trigger_id}",
            ),
            self._channel.context(),
        )
        return TriggerView.model_validate(response.body)

    async def control(self, trigger_id: UUID, request: TriggerControl) -> TriggerView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/orchestration/triggers/{trigger_id}/status",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return TriggerView.model_validate(response.body)

    async def webhook(self, trigger_id: UUID) -> TriggerAccepted:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/orchestration/triggers/{trigger_id}/webhook",
            ),
            self._channel.context(),
        )
        return TriggerAccepted.model_validate(response.body)


class OrchestrationClient:
    """The orchestration API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.capability_bindings = OrchestrationCapabilityBindingsClient(channel)
        self.configurations = OrchestrationConfigurationsClient(channel)
        self.definitions = OrchestrationDefinitionsClient(channel)
        self.environments = OrchestrationEnvironmentsClient(channel)
        self.flows = OrchestrationFlowsClient(channel)
        self.mcp_connections = OrchestrationMcpConnectionsClient(channel)
        self.model_operations = OrchestrationModelOperationsClient(channel)
        self.runs = OrchestrationRunsClient(channel)
        self.sessions = OrchestrationSessionsClient(channel)
        self.triggers = OrchestrationTriggersClient(channel)


def create_orchestration_client(channel: GroupChannel) -> OrchestrationClient:
    """Create the orchestration group client over one composed platform channel."""
    return OrchestrationClient(channel)


__all__ = [
    "AgentConfiguration",
    "AgentGraphSpec",
    "AgentTemplate",
    "AssetRef",
    "BindingConfiguration",
    "BindingConnect",
    "BindingCreate",
    "BindingUpdate",
    "BindingView",
    "CancelRequest",
    "CapabilitySelection",
    "ConfigurationCloseRequest",
    "ConfigurationClosureView",
    "ConfigurationCreate",
    "ConfigurationHook",
    "ConfigurationInvitationCreate",
    "ConfigurationInvitationView",
    "ConfigurationMembershipView",
    "ConfigurationRevisionView",
    "ConfigurationRunCreate",
    "ConfigurationSpec",
    "ConfigurationUpdate",
    "ConfigurationView",
    "DefinitionCreate",
    "DefinitionGrantRequest",
    "DefinitionRevisionView",
    "DefinitionUpdate",
    "DefinitionView",
    "EnvironmentCommandRequest",
    "EnvironmentCreateRequest",
    "EnvironmentFileDownloadRequest",
    "EnvironmentFileUploadRequest",
    "EnvironmentView",
    "EventPage",
    "EventView",
    "ExecutionLimits",
    "ExecutionRequest",
    "FileCreateRequest",
    "FileVersionView",
    "FileView",
    "FlowAuthoringContract",
    "FlowCondition",
    "FlowControl",
    "FlowDefinitionSpecInput",
    "FlowDefinitionSpecOutput",
    "FlowEventView",
    "FlowExecutionPolicy",
    "FlowExport",
    "FlowGraphInput",
    "FlowGraphOutput",
    "FlowLimits",
    "FlowNodeInput",
    "FlowNodeOutput",
    "FlowPlanProposal",
    "FlowPlanReview",
    "FlowPlanReviewView",
    "FlowPublishRequest",
    "FlowRunCreate",
    "FlowRunView",
    "FlowSignal",
    "FlowStepView",
    "FlowValidationResult",
    "HistoryAppendRequest",
    "HistoryAppendView",
    "HistoryItemView",
    "HistoryPage",
    "HookBudget",
    "InstalledExport",
    "InstalledHook",
    "MCPBinding",
    "MCPCreateRequest",
    "MCPToolsView",
    "MCPView",
    "ModelConfiguration",
    "ModelOperationReconciliation",
    "ModelOperationRequest",
    "ModelOperationView",
    "NormalizedUsage",
    "OperationConfirmation",
    "OperationView",
    "OrchestrationCapabilityBindingsClient",
    "OrchestrationClient",
    "OrchestrationConfigurationsClient",
    "OrchestrationConfigurationsFilesClient",
    "OrchestrationConfigurationsInvitationsClient",
    "OrchestrationConfigurationsMembersClient",
    "OrchestrationConfigurationsRevisionsClient",
    "OrchestrationConfigurationsRunsClient",
    "OrchestrationConfigurationsSessionsClient",
    "OrchestrationDefinitionsClient",
    "OrchestrationDefinitionsGrantsClient",
    "OrchestrationDefinitionsRevisionsClient",
    "OrchestrationEnvironmentsClient",
    "OrchestrationEnvironmentsCommandsClient",
    "OrchestrationEnvironmentsFilesClient",
    "OrchestrationEnvironmentsOperationsClient",
    "OrchestrationFlowsClient",
    "OrchestrationFlowsEventsClient",
    "OrchestrationFlowsPlansClient",
    "OrchestrationFlowsSignalsClient",
    "OrchestrationFlowsStepsClient",
    "OrchestrationMcpConnectionsClient",
    "OrchestrationMcpConnectionsOperationsClient",
    "OrchestrationMcpConnectionsToolsClient",
    "OrchestrationModelOperationsClient",
    "OrchestrationRunsClient",
    "OrchestrationRunsEventsClient",
    "OrchestrationSessionsClient",
    "OrchestrationSessionsHistoryClient",
    "OrchestrationTriggersClient",
    "ReconcileRequest",
    "ResumeRequest",
    "RunOutput",
    "RunView",
    "RuntimeControlRequest",
    "RuntimeOperationView",
    "RuntimeReconcileRequest",
    "SessionCreateRequest",
    "SessionView",
    "TriggerAccepted",
    "TriggerConfiguration",
    "TriggerControl",
    "TriggerCreate",
    "TriggerView",
    "create_orchestration_client",
    "opsmesh__orchestration__execution__contracts__ToolConfiguration",
]
