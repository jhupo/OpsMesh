from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.agents.payloads import profile_snapshot
from backend.app.agents.versions import AgentVersionRecorder
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    RESOURCE_STATUS_REJECTED,
)

AGENT_STATUS_ACTIVE = RESOURCE_STATUS_ACTIVE
AGENT_STATUS_ARCHIVED = "archived"


class AgentProfileLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._versions = AgentVersionRecorder(session)

    def archive_agent(
        self,
        profile: AgentProfile,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        if profile.status == AGENT_STATUS_ARCHIVED:
            return profile
        before_snapshot = profile_snapshot(profile)
        profile.status = AGENT_STATUS_ARCHIVED
        profile.archived_at = datetime.now(UTC)
        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile archived",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.archived",
            changed_fields=["archived_at", "status"],
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def activate_agent(
        self,
        profile: AgentProfile,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        if profile.status == AGENT_STATUS_ACTIVE:
            return profile
        if profile.status in {RESOURCE_STATUS_PENDING_APPROVAL, RESOURCE_STATUS_REJECTED}:
            raise ValueError("Agent profile requires approval before activation")
        before_snapshot = profile_snapshot(profile)
        profile.status = AGENT_STATUS_ACTIVE
        profile.archived_at = None
        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile activated",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.activated",
            changed_fields=["archived_at", "status"],
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def delete_agent(self, profile: AgentProfile) -> bool:
        self._session.delete(profile)
        self._session.commit()
        return True
