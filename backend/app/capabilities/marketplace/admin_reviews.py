"""Platform decisions reuse the existing resource publication approval lifecycle."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.marketplace.models import MarketplaceListing, TalentListing
from backend.app.governance.reviews.resource_review_targets import ResourceReviewDecisionService
from backend.app.identity.auth.service import AuthenticationService
from backend.app.identity.authorization.context import AuthenticatedUser
from backend.app.orchestration.approvals.decisions import ApprovalDecisionService
from backend.app.orchestration.approvals.models import Approval
from backend.app.shared.db.pagination import page_scalars
from backend.app.shared.errors import ConflictError, ForbiddenError, NotFoundError
from backend.app.shared.pagination import PageParams

MARKET_REVIEW_TARGETS = ("marketplace_listing", "talent_listing")


class AdminMarketplaceReviewService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_reviews(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None,
        status: str | None,
    ) -> tuple[list[Approval], int]:
        statement = select(Approval).where(
            Approval.payload["kind"].as_string() == "resource_review",
            Approval.payload["target_type"].as_string().in_(MARKET_REVIEW_TARGETS),
            Approval.agent_run_id.is_(None),
            Approval.task_id.is_(None),
        )
        if workspace_id is not None:
            statement = statement.where(Approval.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Approval.status == status)
        return page_scalars(
            self._session, statement.order_by(Approval.created_at, Approval.id), page
        )

    def decide(
        self,
        workspace_id: UUID,
        approval_id: UUID,
        *,
        actor: AuthenticatedUser,
        approved: bool,
        reason: str,
    ) -> Approval:
        actor = AuthenticationService(self._session).refresh_authenticated_user(actor)
        if not actor.platform_admin or actor.uses_restricted_token:
            raise ForbiddenError("An unrestricted platform administrator account is required")
        approval = self._session.scalar(
            select(Approval)
            .where(
                Approval.id == approval_id,
                Approval.workspace_id == workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            approval is None
            or approval.payload.get("kind") != "resource_review"
            or approval.agent_run_id is not None
            or approval.task_id is not None
        ):
            raise NotFoundError("Market publication review not found")
        target_type = str(approval.payload.get("target_type"))
        if target_type not in MARKET_REVIEW_TARGETS:
            raise NotFoundError("Market publication review not found")
        try:
            target_id = UUID(str(approval.payload.get("target_id")))
        except ValueError as exc:
            raise ConflictError("Invalid review target") from exc
        target = ResourceReviewDecisionService(self._session).target(
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=target_id,
        )
        if not isinstance(target, MarketplaceListing | TalentListing):
            raise ConflictError("Review target no longer exists")
        self._session.refresh(target, with_for_update=True)
        if approval.status == "pending":
            snapshot = approval.payload.get("snapshot")
            if (
                target.status != "pending_approval"
                or not isinstance(snapshot, dict)
                or snapshot.get("version") != target.version
            ):
                raise ConflictError("Listing changed after review was requested")
        decision = ApprovalDecisionService(self._session)
        try:
            return (decision.approve if approved else decision.reject)(
                approval, actor.user_id, reason
            )
        except ValueError as exc:
            self._session.rollback()
            raise ConflictError(str(exc)) from exc
