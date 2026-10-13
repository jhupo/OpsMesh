"""Platform session identity and governance; SDK tables own message persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentSessionScope
from opsmesh.agents.sessions.models import (
    ACTIVE_SESSION_STATUS,
    AgentSessionIdentity,
    AgentSessionMetadata,
)


def prepare_session_binding(
    session: Session,
    *,
    ref: AgentSessionIdentity,
    agent_profile_id: UUID,
    agent_team_id: UUID | None,
    task_id: UUID | None,
    metadata: dict[str, object],
) -> AgentSessionScope:
    def find() -> AgentSessionMetadata | None:
        return session.scalar(
            select(AgentSessionMetadata).where(
                AgentSessionMetadata.workspace_id == ref.workspace_id,
                AgentSessionMetadata.session_key == ref.session_key,
            )
        )

    existing = find()
    if existing is None:
        try:
            with session.begin_nested():
                session.add(
                    AgentSessionMetadata(
                        workspace_id=ref.workspace_id,
                        session_key=ref.session_key,
                        scope_type=ref.scope_type,
                        scope_id=ref.scope_id,
                        agent_profile_id=agent_profile_id,
                        agent_team_id=agent_team_id,
                        task_id=task_id,
                        session_metadata=metadata,
                    )
                )
                session.flush()
        except IntegrityError:
            if find() is None:
                raise
        existing = find()
    if existing is None:
        raise ValueError("SDK session metadata was not created")
    if existing.status != ACTIVE_SESSION_STATUS:
        raise ValueError("SDK session is not active")
    return AgentSessionScope(
        session_id=ref.session_key,
        workspace_id=ref.workspace_id,
    )
