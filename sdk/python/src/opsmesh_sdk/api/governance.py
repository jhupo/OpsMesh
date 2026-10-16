"""Generated governance API group; regenerate instead of editing by hand.

Produced by scripts/generate_sdk_api.py from docs/openapi.json and
sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.
Stable operation ids of this group:

- ``governance.approvals.list``
- ``governance.approvals.read``
- ``governance.approvals.approve``
- ``governance.approvals.reject``
- ``governance.audit.events.list``
- ``governance.audit.events.record``
- ``governance.audit.events.read``
- ``governance.audit.verifications.create``
- ``governance.audit.verifications.read``
- ``governance.security_events.list``
- ``governance.security_events.read``
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from opsmesh_sdk.transport.contracts import (
    GroupChannel,
    TransportRequest,
)


class ApprovalDecisionRequest(BaseModel):
    """One decision; the idempotency key makes its retry replayable."""

    idempotency_key: str
    reason: str | None = None


class ApprovalRequestView(BaseModel):
    """One approval request; the presented status derives expiry."""

    approval_id: UUID
    approver_user_id: UUID
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision_reason: str | None
    details: dict[str, object]
    expires_at: datetime | None
    kind: str
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
    """One sanitized audit fact; the digest columns prove its chain position."""

    action: str
    actor_id: str | None
    actor_type: str
    configuration_revision: str | None
    details: dict[str, object]
    event_hash: str
    event_id: UUID
    evidence_refs: list[str]
    installation_id: UUID | None
    instance_id: UUID | None
    invocation_id: UUID | None
    occurred_at: datetime
    package_key: str | None
    previous_hash: str
    project_id: UUID | None
    sequence: int
    source: str
    summary: str
    target_id: str
    target_type: str
    workspace_id: UUID | None


class AuditVerificationRequest(BaseModel):
    from_sequence: int
    to_sequence: int


class AuditVerificationView(BaseModel):
    checked_count: int
    created_at: datetime
    failure_reason: str | None
    first_broken_sequence: int | None
    from_sequence: int
    status: str
    to_sequence: int
    verification_id: UUID


class ExtensionAuditAppend(BaseModel):
    """One extension-reported fact; actor and scope come from the verified call."""

    action: str
    details: dict[str, object] | None = None
    evidence_refs: list[str] | None = None
    idempotency_key: str
    summary: str
    target_id: str
    target_type: str


class SecurityEventView(BaseModel):
    """One sanitized security fact; details never carry credentials."""

    created_at: datetime
    details: dict[str, object]
    event_id: UUID
    kind: str
    occurred_at: datetime
    severity: str
    summary: str
    user_id: UUID | None


class ApprovalPage(BaseModel):
    approvals: list[ApprovalRequestView]
    total: int


class AuditEventPage(BaseModel):
    """One cursor page of audit events ordered by chain sequence."""

    events: list[AuditEventView]
    next_after_sequence: int | None


class SecurityEventPage(BaseModel):
    events: list[SecurityEventView]
    total: int


class GovernanceApprovalsClient:
    """Generated client for the ``governance.approvals`` operations."""

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
                path=f"{self._channel.api_root}/governance/approvals",
                query=query,
            ),
            self._channel.context(),
        )
        return ApprovalPage.model_validate(response.body)

    async def read(self, approval_id: UUID) -> ApprovalRequestView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/approvals/{approval_id}",
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
                path=f"{self._channel.api_root}/governance/approvals/{approval_id}/approve",
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
                path=f"{self._channel.api_root}/governance/approvals/{approval_id}/reject",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return ApprovalRequestView.model_validate(response.body)


class GovernanceAuditEventsClient:
    """Generated client for the ``governance.audit.events`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        after_sequence: int | None = None,
        actor_type: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        workspace_id: UUID | None = None,
        project_id: UUID | None = None,
        limit: int = 50,
    ) -> AuditEventPage:
        """One cursor page ordered by chain sequence; filters are ANDed."""
        query = {
            name: str(value)
            for name, value in (
                ("after_sequence", after_sequence),
                ("actor_type", actor_type),
                ("action", action),
                ("target_type", target_type),
                ("target_id", target_id),
                ("workspace_id", workspace_id),
                ("project_id", project_id),
                ("limit", limit),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/audit/events",
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
                path=f"{self._channel.api_root}/governance/audit/events",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return AuditAppendView.model_validate(response.body)

    async def read(self, event_id: UUID) -> AuditEventView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/audit/events/{event_id}",
            ),
            self._channel.context(),
        )
        return AuditEventView.model_validate(response.body)


class GovernanceAuditVerificationsClient:
    """Generated client for the ``governance.audit.verifications`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def create(self, request: AuditVerificationRequest) -> AuditVerificationView:
        """Register one verification run and stage its durable execution."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/governance/audit/verifications",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return AuditVerificationView.model_validate(response.body)

    async def read(self, verification_id: UUID) -> AuditVerificationView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/audit/verifications/{verification_id}",
            ),
            self._channel.context(),
        )
        return AuditVerificationView.model_validate(response.body)


class GovernanceAuditClient:
    """Generated client for the ``governance.audit`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.events = GovernanceAuditEventsClient(channel)
        self.verifications = GovernanceAuditVerificationsClient(channel)


class GovernanceSecurityEventsClient:
    """Generated client for the ``governance.security_events`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        kind: str | None = None,
        severity: str | None = None,
        user_id: UUID | None = None,
    ) -> SecurityEventPage:
        """One offset page of security facts; filters are ANDed server-side."""
        query = {
            name: str(value)
            for name, value in (
                ("limit", limit),
                ("offset", offset),
                ("kind", kind),
                ("severity", severity),
                ("user_id", user_id),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/security-events",
                query=query,
            ),
            self._channel.context(),
        )
        return SecurityEventPage.model_validate(response.body)

    async def read(self, event_id: UUID) -> SecurityEventView:
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/security-events/{event_id}",
            ),
            self._channel.context(),
        )
        return SecurityEventView.model_validate(response.body)


class GovernanceClient:
    """The governance API group."""

    def __init__(self, channel: GroupChannel) -> None:
        self.approvals = GovernanceApprovalsClient(channel)
        self.audit = GovernanceAuditClient(channel)
        self.security_events = GovernanceSecurityEventsClient(channel)


def create_governance_client(channel: GroupChannel) -> GovernanceClient:
    """Create the governance group client over one composed platform channel."""
    return GovernanceClient(channel)


__all__ = [
    "ApprovalDecisionRequest",
    "ApprovalPage",
    "ApprovalRequestView",
    "AuditAppendView",
    "AuditEventPage",
    "AuditEventView",
    "AuditVerificationRequest",
    "AuditVerificationView",
    "ExtensionAuditAppend",
    "GovernanceApprovalsClient",
    "GovernanceAuditClient",
    "GovernanceAuditEventsClient",
    "GovernanceAuditVerificationsClient",
    "GovernanceClient",
    "GovernanceSecurityEventsClient",
    "SecurityEventPage",
    "SecurityEventView",
    "create_governance_client",
]
