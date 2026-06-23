from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from backend.app.agent_messages.constants import PENDING_STATUSES, READ_STATUSES
from backend.app.agent_messages.models import AgentMessage, AgentMessageThread


class AgentMailboxSummaryMixin:
    def get_summary(self, workspace_id: UUID, *, latest_limit: int = 20) -> dict[str, object]:
        limit = max(latest_limit, 0)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "thread_count": self._count_threads(workspace_id),
            "message_count": self._count_messages(workspace_id),
            "unread_count": self._count_unread_messages(workspace_id),
            "pending_count": self._count_pending_messages(workspace_id),
            "thread_status_counts": self._thread_status_counts(workspace_id),
            "message_status_counts": self._message_status_counts(workspace_id),
            "latest_messages_by_task": self._latest_messages_by_task(workspace_id, limit),
            "latest_messages_by_team": self._latest_messages_by_team(workspace_id, limit),
        }

    def _count_threads(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(AgentMessageThread.workspace_id == workspace_id)
        )
        return int(total or 0)

    def _count_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(AgentMessage.workspace_id == workspace_id)
        )
        return int(total or 0)

    def _count_unread_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status.notin_(READ_STATUSES),
            )
        )
        return int(total or 0)

    def _count_pending_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.status.in_(PENDING_STATUSES),
            )
        )
        return int(total or 0)

    def _thread_status_counts(self, workspace_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(AgentMessageThread.status, func.count())
            .where(AgentMessageThread.workspace_id == workspace_id)
            .group_by(AgentMessageThread.status)
        )
        return {str(status): int(count) for status, count in rows}

    def _message_status_counts(self, workspace_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(AgentMessage.status, func.count())
            .where(AgentMessage.workspace_id == workspace_id)
            .group_by(AgentMessage.status)
        )
        return {str(status): int(count) for status, count in rows}

    def _latest_messages_by_task(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> list[dict[str, object]]:
        if limit == 0:
            return []
        rank = func.row_number().over(
            partition_by=AgentMessage.task_id,
            order_by=(AgentMessage.created_at.desc(), AgentMessage.id.desc()),
        )
        ranked = (
            select(AgentMessage.id.label("message_id"), rank.label("rank"))
            .where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.task_id.is_not(None),
            )
            .subquery()
        )
        rows = self._session.execute(
            select(AgentMessage)
            .join(ranked, ranked.c.message_id == AgentMessage.id)
            .where(ranked.c.rank == 1)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(limit)
        )
        return [
            {"task_id": message.task_id, "message": message}
            for message in rows.scalars()
            if message.task_id is not None
        ]

    def _latest_messages_by_team(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> list[dict[str, object]]:
        if limit == 0:
            return []
        rank = func.row_number().over(
            partition_by=AgentMessage.agent_team_id,
            order_by=(AgentMessage.created_at.desc(), AgentMessage.id.desc()),
        )
        ranked = (
            select(
                AgentMessage.id.label("message_id"),
                AgentMessage.agent_team_id.label("team_id"),
                rank.label("rank"),
            )
            .where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id.is_not(None),
            )
            .subquery()
        )
        rows = self._session.execute(
            select(AgentMessage, ranked.c.team_id)
            .join(ranked, ranked.c.message_id == AgentMessage.id)
            .where(ranked.c.rank == 1)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(limit)
        )
        return [{"team_id": team_id, "message": message} for message, team_id in rows]
