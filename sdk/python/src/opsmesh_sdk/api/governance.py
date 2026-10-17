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
- ``governance.costs.pricing.list``
- ``governance.costs.pricing.publish``
- ``governance.costs.budget.read``
- ``governance.costs.budget.upsert``
- ``governance.costs.reservations.list``
- ``governance.costs.usage.list``
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


class BudgetUpsertRequest(BaseModel):
    """Create or change one workspace budget; a change states its revision."""

    currency: str
    enforcement: str = 'block'
    expected_version: int | None = None
    limit_amount: float | str | None = None
    period: str = 'monthly'
    unlimited: bool = False


class BudgetView(BaseModel):
    budget_id: UUID
    budget_state: str
    currency: str
    current_window_key: str
    enforcement: str
    limit_amount: str | None
    period: str
    revision: int
    unlimited: bool
    unlimited_decided_at: datetime | None
    unlimited_decided_by_user_id: UUID | None
    updated_at: datetime
    window_reserved: str
    window_used: str
    workspace_id: UUID


class ExtensionAuditAppend(BaseModel):
    """One extension-reported fact; actor and scope come from the verified call."""

    action: str
    details: dict[str, object] | None = None
    evidence_refs: list[str] | None = None
    idempotency_key: str
    summary: str
    target_id: str
    target_type: str


class NormalizedUsage(BaseModel):
    """The standard usage snapshot of one priced operation.

One shape serves models (token counts) and tools (invocation count);
a model operation leaves the tool fields zero and vice versa."""

    cached_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_invocations: int = 0


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


class UnitPricesInput(BaseModel):
    """The pinned price sheet of one subject, per single unit."""

    cached_input_price: float | str | None = None
    currency: str
    input_price: float | str = '0'
    output_price: float | str = '0'
    tool_unit_price: float | str | None = None


class UnitPricesOutput(BaseModel):
    """The pinned price sheet of one subject, per single unit."""

    cached_input_price: str | None = None
    currency: str
    input_price: str = '0'
    output_price: str = '0'
    tool_unit_price: str | None = None


class ApprovalPage(BaseModel):
    approvals: list[ApprovalRequestView]
    total: int


class AuditEventPage(BaseModel):
    """One cursor page of audit events ordered by chain sequence."""

    events: list[AuditEventView]
    next_after_sequence: int | None


class PricingVersionPublishRequest(BaseModel):
    """Publish one immutable price sheet for one model or tool subject."""

    prices: UnitPricesInput
    subject_key: str
    subject_kind: str


class PricingVersionView(BaseModel):
    created_at: datetime
    digest: str
    prices: UnitPricesOutput
    pricing_version_id: UUID
    published_by_user_id: UUID | None
    status: str
    subject_key: str
    subject_kind: str
    version: int


class ReservationView(BaseModel):
    """One held or closed cost reservation; the pending set reads this."""

    amount: str
    budget_state: str
    created_at: datetime
    currency: str
    max_cost: str
    operation_id: str
    overage_amount: str | None
    prices: UnitPricesOutput
    pricing_version_id: UUID
    released_at: datetime | None
    released_reason: str | None
    run_ref: str | None
    settled_amount: str | None
    settled_at: datetime | None
    status: str
    subject_key: str
    subject_kind: str
    uncertain_reason: str | None
    window_key: str | None
    workspace_id: UUID | None


class SecurityEventPage(BaseModel):
    events: list[SecurityEventView]
    total: int


class UsageView(BaseModel):
    actual_cost: str
    operation_id: str
    overage_amount: str
    pricing_version_id: UUID
    recorded_at: datetime
    subject_key: str
    subject_kind: str
    usage: NormalizedUsage


class PricingVersionPage(BaseModel):
    total: int
    versions: list[PricingVersionView]


class ReservationPage(BaseModel):
    reservations: list[ReservationView]
    total: int


class UsagePage(BaseModel):
    total: int
    usages: list[UsageView]


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


class GovernanceCostsBudgetClient:
    """Generated client for the ``governance.costs.budget`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def read(self, workspace_id: UUID, *, currency: str) -> BudgetView:
        """Read one workspace budget; no budget is the unconfigured state."""
        query = {
            name: str(value)
            for name, value in (
                ("currency", currency),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/costs/workspaces/{workspace_id}/budget",
                query=query,
            ),
            self._channel.context(),
        )
        return BudgetView.model_validate(response.body)

    async def upsert(self, workspace_id: UUID, request: BudgetUpsertRequest) -> BudgetView:
        """Create or change one workspace budget under its expected revision."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="PUT",
                path=f"{self._channel.api_root}/governance/costs/workspaces/{workspace_id}/budget",
                body=request.model_dump(mode="json", exclude_none=True),
            ),
            self._channel.context(),
        )
        return BudgetView.model_validate(response.body)


class GovernanceCostsPricingClient:
    """Generated client for the ``governance.costs.pricing`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        *,
        subject_kind: str | None = None,
        subject_key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PricingVersionPage:
        """List the published price sheets, newest version first per subject."""
        query = {
            name: str(value)
            for name, value in (
                ("subject_kind", subject_kind),
                ("subject_key", subject_key),
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/costs/pricing/versions",
                query=query,
            ),
            self._channel.context(),
        )
        return PricingVersionPage.model_validate(response.body)

    async def publish(self, request: PricingVersionPublishRequest) -> PricingVersionView:
        """Publish one immutable price sheet for one model or tool subject."""
        response = await self._channel.transport.send(
            TransportRequest(
                method="POST",
                path=f"{self._channel.api_root}/governance/costs/pricing/versions",
                body=request.model_dump(mode="json"),
            ),
            self._channel.context(),
        )
        return PricingVersionView.model_validate(response.body)


class GovernanceCostsReservationsClient:
    """Generated client for the ``governance.costs.reservations`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(
        self,
        workspace_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ReservationPage:
        """List one workspace's reservations; the uncertain set is the pending work."""
        query = {
            name: str(value)
            for name, value in (
                ("status", status),
                ("limit", limit),
                ("offset", offset),
            )
            if value is not None
        }
        response = await self._channel.transport.send(
            TransportRequest(
                method="GET",
                path=f"{self._channel.api_root}/governance/costs/workspaces/{workspace_id}/reservations",
                query=query,
            ),
            self._channel.context(),
        )
        return ReservationPage.model_validate(response.body)


class GovernanceCostsUsageClient:
    """Generated client for the ``governance.costs.usage`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel

    async def list(self, workspace_id: UUID, *, limit: int = 50, offset: int = 0) -> UsagePage:
        """List one workspace's settled usage, newest first."""
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
                path=f"{self._channel.api_root}/governance/costs/workspaces/{workspace_id}/usage",
                query=query,
            ),
            self._channel.context(),
        )
        return UsagePage.model_validate(response.body)


class GovernanceCostsClient:
    """Generated client for the ``governance.costs`` operations."""

    def __init__(self, channel: GroupChannel) -> None:
        self._channel = channel
        self.budget = GovernanceCostsBudgetClient(channel)
        self.pricing = GovernanceCostsPricingClient(channel)
        self.reservations = GovernanceCostsReservationsClient(channel)
        self.usage = GovernanceCostsUsageClient(channel)


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
        self.costs = GovernanceCostsClient(channel)
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
    "BudgetUpsertRequest",
    "BudgetView",
    "ExtensionAuditAppend",
    "GovernanceApprovalsClient",
    "GovernanceAuditClient",
    "GovernanceAuditEventsClient",
    "GovernanceAuditVerificationsClient",
    "GovernanceClient",
    "GovernanceCostsBudgetClient",
    "GovernanceCostsClient",
    "GovernanceCostsPricingClient",
    "GovernanceCostsReservationsClient",
    "GovernanceCostsUsageClient",
    "GovernanceSecurityEventsClient",
    "NormalizedUsage",
    "PricingVersionPage",
    "PricingVersionPublishRequest",
    "PricingVersionView",
    "ReservationPage",
    "ReservationView",
    "SecurityEventPage",
    "SecurityEventView",
    "UnitPricesInput",
    "UnitPricesOutput",
    "UsagePage",
    "UsageView",
    "create_governance_client",
]
