from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.shared.utils import coerce_int_or_zero
from opsmesh.teams.management.models import (
    TEAM_RUNTIME_STATUS_KEY,
)
from opsmesh.teams.sessions.mailbox import TeamMailboxStore
from opsmesh.teams.sessions.refs import team_runtime_metadata
from opsmesh.teams.sessions.repository import TeamOperationRepository


class TeamAdvancementRecorder:
    """Record explicit Team orchestration progress."""

    def __init__(
        self,
        *,
        session: Session,
        repo: TeamOperationRepository,
        mailbox: TeamMailboxStore,
    ) -> None:
        self._session = session
        self._repo = repo
        self._mailbox = mailbox

    def record_advancement(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        team = self._repo.team(workspace_id, team_id)
        if team is None:
            return
        policy = dict(team.default_task_policy or {})
        runtime_metadata = team_runtime_metadata(team)
        recorded_at = datetime.now(UTC).isoformat()
        advancement_count = coerce_int_or_zero(runtime_metadata.get("advancement_count")) + 1
        last_advancement: dict[str, object] = {
            "advancement": advancement_count,
            "status": status,
            "summary": dict(summary),
            "recorded_at": recorded_at,
            "actor_user_id": str(actor_user_id),
        }
        runtime_metadata.update(
            {
                "last_advancement": last_advancement,
                "advancement_count": advancement_count,
            }
        )
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        thread = self._mailbox._get_or_create_thread(team)
        self._mailbox._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type="runtime_advancement",
            body=f"Team orchestration advancement: {status}",
            payload={"status": status, "summary": summary, "advancement": last_advancement},
        )
        self._session.flush()
