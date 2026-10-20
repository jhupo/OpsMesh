"""Generated platform API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``platform.health.live``
- ``platform.health.ready``
- ``platform.approvals.list``
- ``platform.approvals.read``
- ``platform.approvals.approve``
- ``platform.approvals.reject``
- ``platform.audit.list``
- ``platform.audit.record``
- ``platform.audit.read``
- ``platform.model_connections.list``
- ``platform.model_connections.create``
- ``platform.model_connections.available``
- ``platform.model_connections.disable``
- ``platform.model_connections.read``
- ``platform.model_connections.update``
- ``platform.model_connections.probe``
- ``platform.model_connections.revoke``
- ``platform.model_connections.grant``
- ``platform.operations.submit``
- ``platform.operations.admission``
- ``platform.operations.get``
- ``platform.operations.cancel``
- ``platform.operations.resume``
- ``platform.policies.list``
- ``platform.policies.evaluate``
- ``platform.policies.put``
- ``platform.system.version``
"""

from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
    decode_array,
)


class ApprovalDecisionRequest(BaseModel):
    """One decision; the idempotency key makes its retry replayable."""

    idempotency_key: str
    reason: str | None = None


class ApprovalRequestView(BaseModel):
    """One approval request; the presented status derives expiry."""

    approval_id: UUID
    approver_role: str | None
    approver_user_id: UUID | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision_reason: str | None
    details: dict[str, object]
    expires_at: datetime | None
    kind: str
    request_key: str
    requester_user_id: UUID | None
    status: str
    summary: str
    target_id: str
    target_type: str
    target_version: str | None
    title: str


class AuditAppendView(BaseModel):
    """The receipt of one extension append; a replayed key reports no new fact."""

    event_id: UUID
    replayed: bool


class AuditEventView(BaseModel):
    """One sanitized audit fact with its database-assigned occurrence sequence."""

    action: str
    actor_id: str | None
    actor_type: str
    category: str
    causation_id: UUID | None
    configuration_id: UUID | None
    configuration_revision: str | None
    correlation_id: str | None
    details: dict[str, object]
    event_id: UUID
    evidence_refs: list[str]
    installation_id: UUID | None
    instance_id: UUID | None
    invocation_id: UUID | None
    occurred_at: datetime
    operation_id: str | None
    package_key: str | None
    recorded_at: datetime
    schema_version: int
    sequence: int
    severity: str
    source: str
    summary: str
    target_id: str
    target_type: str
    trace_id: str | None


class AvailableModelConnectionView(BaseModel):
    """The user's selectable model subset; no upstream address or secret."""

    connection_id: UUID
    default_model: str
    is_default: bool
    models: list[str]
    name: str
    provider: str
    revision: int


class ExtensionAuditAppend(BaseModel):
    """One extension-reported fact; actor and scope come from the verified call."""

    action: str
    details: dict[str, object] | None = None
    evidence_refs: list[str] | None = None
    idempotency_key: str
    summary: str
    target_id: str
    target_type: str


class HealthView(BaseModel):
    status: str


class ModelConnectionCreateRequest(BaseModel):
    api_key: str
    base_url: str = 'https://api.openai.com/v1'
    default_model: str
    models: list[str]
    name: str


class ModelConnectionUpdateRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    expected_revision: int
    models: list[str] | None = None
    name: str | None = None


class ModelConnectionView(BaseModel):
    base_url: str
    connection_id: UUID
    created_at: datetime
    default_model: str
    last_probe_at: datetime | None
    last_probe_detail: str | None
    last_probe_status: str
    models: list[str]
    name: str
    provider: str
    revision: int
    status: str
    updated_at: datetime


class ModelGrantRequest(BaseModel):
    is_default: bool = False
    models: list[str]


class ModelGrantView(BaseModel):
    connection_id: UUID
    is_default: bool
    models: list[str]
    user_id: UUID


class PlatformAdmissionView(BaseModel):
    active: dict[str, int]
    drained: bool


class PlatformOperationCreate(BaseModel):
    command: str
    idempotency_key: str
    owner: str
    target: dict[str, object] | None = None


class PlatformOperationView(BaseModel):
    command: str
    error_detail: str | None
    failure_code: str | None
    id: UUID
    idempotency_key: str
    owner: str
    requested_by_user_id: UUID
    result: dict[str, object]
    status: str
    target: dict[str, object]


class PolicyDecision(BaseModel):
    decision: str


class PolicyEvaluation(BaseModel):
    action: str
    name: str


class RiskRule(BaseModel):
    action: str
    decision: str
    name: str | None = None


class VersionView(BaseModel):
    build: str | None
    environment: str
    service: str
    version: str


class ApprovalPage(BaseModel):
    approvals: list[ApprovalRequestView]
    total: int


class AuditEventPage(BaseModel):
    """One cursor page of audit events ordered by occurrence sequence."""

    events: list[AuditEventView]
    next_after_sequence: int | None


class ModelConnectionPage(BaseModel):
    connections: list[ModelConnectionView]
    total: int


class RiskPolicy(BaseModel):
    default: str = 'allow'
    rules: list[RiskRule] | None = None


class PolicyView(BaseModel):
    enabled: bool
    id: UUID
    key: str
    policy: RiskPolicy
    revision: int
    updated_at: datetime


class PolicyWrite(BaseModel):
    enabled: bool = True
    expected_revision: int
    policy: RiskPolicy


class PlatformApprovalsClient:
    """Generated client for the ``platform.approvals`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> ApprovalPage:
        """The requests assigned to the current subject."""
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("status", status),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/approvals",
                query=query,
            ),
            self._channel.context(),
        )
        return ApprovalPage.model_validate(response.body)

    async def read(self, approval_id: UUID) -> ApprovalRequestView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/approvals/{approval_id}",
            ),
            self._channel.context(),
        )
        return ApprovalRequestView.model_validate(response.body)

    async def approve(
        self,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> ApprovalRequestView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/approvals/{approval_id}/approve",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ApprovalRequestView.model_validate(response.body)

    async def reject(
        self,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> ApprovalRequestView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/approvals/{approval_id}/reject",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ApprovalRequestView.model_validate(response.body)


class PlatformAuditClient:
    """Generated client for the ``platform.audit`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        after_sequence: int | None = None,
        category: str | None = None,
        severity: str | None = None,
        actor_type: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        configuration_id: UUID | None = None,
        limit: int = 50,
    ) -> AuditEventPage:
        """One cursor page ordered by occurrence sequence; filters are ANDed."""
        query = {
            name: str(value)
            for name, value in (
                ("after_sequence", after_sequence),
                ("category", category),
                ("severity", severity),
                ("actor_type", actor_type),
                ("action", action),
                ("target_type", target_type),
                ("target_id", target_id),
                ("configuration_id", configuration_id),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/audit",
                query=query,
            ),
            self._channel.context(),
        )
        return AuditEventPage.model_validate(response.body)

    async def record(self, request: ExtensionAuditAppend) -> AuditAppendView:
        """Append one extension-reported fact under the verified invocation scope."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/audit",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return AuditAppendView.model_validate(response.body)

    async def read(self, event_id: UUID) -> AuditEventView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/audit/{event_id}",
            ),
            self._channel.context(),
        )
        return AuditEventView.model_validate(response.body)


class PlatformHealthClient:
    """Generated client for the ``platform.health`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def live(self) -> HealthView:
        """Liveness never touches a dependency and is safe for process probes."""
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"{self._channel.api_root}/health"),
            self._channel.context(),
        )
        return HealthView.model_validate(response.body)

    async def ready(self) -> HealthView:
        """Readiness verifies DB, Redis and the native Temporal service."""
        response = await self._channel.transport.send(
            TransportRequest(method="GET", path=f"{self._channel.api_root}/health/ready"),
            self._channel.context(),
        )
        return HealthView.model_validate(response.body)


class PlatformModelConnectionsClient:
    """Generated client for the ``platform.model_connections`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, *, limit: int = 50, offset: int = 0) -> ModelConnectionPage:
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/model-connections",
                query=query,
            ),
            self._channel.context(),
        )
        return ModelConnectionPage.model_validate(response.body)

    async def create(self, request: ModelConnectionCreateRequest) -> ModelConnectionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/model-connections",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelConnectionView.model_validate(response.body)

    async def available(self) -> builtins.list[AvailableModelConnectionView]:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/model-connections/available",
            ),
            self._channel.context(),
        )
        return [
            AvailableModelConnectionView.model_validate(item)
            for item in decode_array(response)
        ]

    async def disable(self, connection_id: UUID) -> ModelConnectionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}",
            ),
            self._channel.context(),
        )
        return ModelConnectionView.model_validate(response.body)

    async def read(self, connection_id: UUID) -> ModelConnectionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}",
            ),
            self._channel.context(),
        )
        return ModelConnectionView.model_validate(response.body)

    async def update(
        self,
        connection_id: UUID,
        request: ModelConnectionUpdateRequest,
    ) -> ModelConnectionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PATCH",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelConnectionView.model_validate(response.body)

    async def probe(self, connection_id: UUID) -> ModelConnectionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}/probe",
            ),
            self._channel.context(),
        )
        return ModelConnectionView.model_validate(response.body)

    async def revoke(self, connection_id: UUID, user_id: UUID) -> None:
        await self._channel.transport.send(
            TransportRequest(
                method="DELETE",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}/users/{user_id}",
            ),
            self._channel.context(),
        )

    async def grant(
        self,
        connection_id: UUID,
        user_id: UUID,
        request: ModelGrantRequest,
    ) -> ModelGrantView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/platform/model-connections/{connection_id}/users/{user_id}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ModelGrantView.model_validate(response.body)


class PlatformOperationsClient:
    """Generated client for the ``platform.operations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def submit(self, request: PlatformOperationCreate) -> PlatformOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/operations",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return PlatformOperationView.model_validate(response.body)

    async def admission(self) -> PlatformAdmissionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/operations/admission",
            ),
            self._channel.context(),
        )
        return PlatformAdmissionView.model_validate(response.body)

    async def get(self, operation_id: UUID) -> PlatformOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/operations/{operation_id}",
            ),
            self._channel.context(),
        )
        return PlatformOperationView.model_validate(response.body)

    async def cancel(self, operation_id: UUID) -> PlatformOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/operations/{operation_id}/cancel",
            ),
            self._channel.context(),
        )
        return PlatformOperationView.model_validate(response.body)

    async def resume(self, operation_id: UUID) -> PlatformOperationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/operations/{operation_id}/resume",
            ),
            self._channel.context(),
        )
        return PlatformOperationView.model_validate(response.body)


class PlatformPoliciesClient:
    """Generated client for the ``platform.policies`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        after: str | None = None,
        limit: int = 100,
    ) -> builtins.list[PolicyView]:
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
                path=f"{self._channel.api_root}/platform/policies",
                query=query,
            ),
            self._channel.context(),
        )
        return [PolicyView.model_validate(item) for item in decode_array(response)]

    async def evaluate(self, request: PolicyEvaluation) -> PolicyDecision:
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/platform/policies/evaluate",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return PolicyDecision.model_validate(response.body)

    async def put(self, key: str, request: PolicyWrite) -> PolicyView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/platform/policies/{key}",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return PolicyView.model_validate(response.body)


class PlatformSystemClient:
    """Generated client for the ``platform.system`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def version(self) -> VersionView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/platform/system/version",
            ),
            self._channel.context(),
        )
        return VersionView.model_validate(response.body)


class PlatformClient:
    """The platform API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.approvals = PlatformApprovalsClient(channel)
        self.audit = PlatformAuditClient(channel)
        self.health = PlatformHealthClient(channel)
        self.model_connections = PlatformModelConnectionsClient(channel)
        self.operations = PlatformOperationsClient(channel)
        self.policies = PlatformPoliciesClient(channel)
        self.system = PlatformSystemClient(channel)


def create_platform_client(channel: GroupChannel) -> PlatformClient:
    """Create the platform group client over one composed platform channel."""
    return PlatformClient(channel)


__all__ = [
    "ApprovalDecisionRequest",
    "ApprovalPage",
    "ApprovalRequestView",
    "AuditAppendView",
    "AuditEventPage",
    "AuditEventView",
    "AvailableModelConnectionView",
    "ExtensionAuditAppend",
    "HealthView",
    "ModelConnectionCreateRequest",
    "ModelConnectionPage",
    "ModelConnectionUpdateRequest",
    "ModelConnectionView",
    "ModelGrantRequest",
    "ModelGrantView",
    "PlatformAdmissionView",
    "PlatformApprovalsClient",
    "PlatformAuditClient",
    "PlatformClient",
    "PlatformHealthClient",
    "PlatformModelConnectionsClient",
    "PlatformOperationCreate",
    "PlatformOperationView",
    "PlatformOperationsClient",
    "PlatformPoliciesClient",
    "PlatformSystemClient",
    "PolicyDecision",
    "PolicyEvaluation",
    "PolicyView",
    "PolicyWrite",
    "RiskPolicy",
    "RiskRule",
    "VersionView",
    "create_platform_client",
]
