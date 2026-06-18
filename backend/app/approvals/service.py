from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.approvals.models import Approval


class ApprovalService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_approval(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID | None,
        agent_run_id: UUID | None,
        requested_by_agent_profile_id: UUID | None,
        approval_type: str,
        risk_level: str,
        payload: dict[str, object],
    ) -> Approval:
        approval = Approval(
            workspace_id=workspace_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            requested_by_agent_profile_id=requested_by_agent_profile_id,
            approval_type=approval_type,
            risk_level=risk_level,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        self._session.add(approval)
        self._session.flush()
        return approval
