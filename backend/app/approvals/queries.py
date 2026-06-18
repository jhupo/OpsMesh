from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.approvals.models import Approval
from backend.app.db.pagination import page_scalars

T = TypeVar("T")


class ApprovalQueryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_approvals(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
        *,
        include_resource_reviews: bool = True,
    ) -> tuple[list[Approval], int]:
        statement = select(Approval).where(Approval.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Approval.status == status)
        if not include_resource_reviews:
            payload_kind = Approval.payload["kind"].as_string()
            statement = statement.where(
                or_(
                    payload_kind.is_(None),
                    payload_kind != "resource_review",
                )
            )
        return self._page(statement.order_by(Approval.created_at.desc()), page)

    def get_scoped(self, workspace_id: UUID, approval_id: UUID) -> Approval | None:
        approval = self._session.get(Approval, approval_id)
        if approval is None or approval.workspace_id != workspace_id:
            return None
        return approval

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
