from typing import TypeAlias, cast
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import (
    Capability,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    Skill,
)
from backend.app.core.typing import uuid_or_none
from backend.app.marketplace.models import MarketplaceListing, TalentListing
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_REJECTED,
)

ResourceReviewTarget: TypeAlias = (
    AgentProfile
    | Capability
    | Skill
    | McpServer
    | McpToolAllowlist
    | McpCredentialReference
    | MarketplaceListing
    | TalentListing
)

RESOURCE_REVIEW_TARGET_MODELS: dict[str, type[ResourceReviewTarget]] = {
    "agent_profile": AgentProfile,
    "capability": Capability,
    "skill": Skill,
    "mcp_server": McpServer,
    "mcp_tool_allowlist": McpToolAllowlist,
    "mcp_credential_reference": McpCredentialReference,
    "marketplace_listing": MarketplaceListing,
    "talent_listing": TalentListing,
}


class ResourceReviewDecisionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_decision(self, approval: Approval, *, user_id: UUID, status: str) -> None:
        payload = approval.payload if isinstance(approval.payload, dict) else {}
        if payload.get("kind") != "resource_review":
            return
        target_type = str(payload.get("target_type") or "")
        target_id = uuid_or_none(payload.get("target_id"))
        if target_id is None:
            return
        target = self.target(
            workspace_id=approval.workspace_id,
            target_type=target_type,
            target_id=target_id,
        )
        if target is None:
            return
        next_status = review_target_next_status(target, status)
        target.status = next_status
        AuditService(self._session).record_user_action(
            workspace_id=approval.workspace_id,
            user_id=user_id,
            action=f"{target_type}.{next_status}",
            target_type=target_type,
            target_id=target_id,
            metadata={"approval_id": str(approval.id), "approval_type": approval.approval_type},
        )

    def target(
        self,
        *,
        workspace_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> ResourceReviewTarget | None:
        model = RESOURCE_REVIEW_TARGET_MODELS.get(target_type)
        if model is None:
            return None
        target = cast(ResourceReviewTarget | None, self._session.get(model, target_id))
        if target is None:
            return None
        if isinstance(target, Capability):
            return target
        if resource_target_workspace_id(target) != workspace_id:
            return None
        return target


def review_target_next_status(target: ResourceReviewTarget, approval_status: str) -> str:
    if approval_status != "approved":
        return RESOURCE_STATUS_REJECTED
    if isinstance(target, MarketplaceListing | TalentListing):
        return "public"
    return RESOURCE_STATUS_ACTIVE


def resource_target_workspace_id(target: ResourceReviewTarget) -> UUID | None:
    workspace_id = getattr(target, "workspace_id", None)
    if workspace_id is None:
        workspace_id = getattr(target, "owner_workspace_id", None)
    if workspace_id is None:
        workspace_id = getattr(target, "source_workspace_id", None)
    return workspace_id if isinstance(workspace_id, UUID) else None
