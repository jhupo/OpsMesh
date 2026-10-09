"""Platform session identity and governance; SDK tables own message persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentSessionBinding
from backend.app.agents.sessions.models import (
    PersistentAgentSession,
    PersistentAgentSessionRef,
    SDKAgentMessage,
)


def prepare_session_binding(
    session: Session,
    *,
    ref: PersistentAgentSessionRef,
    agent_profile_id: UUID,
    agent_team_id: UUID | None,
    task_id: UUID | None,
    metadata: dict[str, object],
) -> AgentSessionBinding:
    def find() -> PersistentAgentSession | None:
        return session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == ref.workspace_id,
                PersistentAgentSession.session_key == ref.session_key,
            )
        )

    existing = find()
    if existing is None:
        try:
            with session.begin_nested():
                session.add(
                    PersistentAgentSession(
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
    sdk_provider = metadata["sdk_provider"]
    if existing.session_metadata.get("sdk_provider") != sdk_provider:
        has_history = session.scalar(
            select(SDKAgentMessage.id).where(SDKAgentMessage.session_id == ref.session_key).limit(1)
        )
        if has_history is not None:
            raise ValueError("Clear the session before changing its SDK provider")
        existing.session_metadata = {**existing.session_metadata, "sdk_provider": sdk_provider}
    return AgentSessionBinding(
        session_id=ref.session_key,
        workspace_id=ref.workspace_id,
        database_url=session.get_bind().engine.url.render_as_string(hide_password=False),
    )
