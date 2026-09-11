from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.model_validation import AgentModelValidator
from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.agents.payloads import profile_audit_state, profile_snapshot
from backend.app.observability.audit_service import AuditService


class AgentVersionRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._model_validator = AgentModelValidator(session)

    def bump_version(
        self,
        profile: AgentProfile,
        *,
        changed_by_user_id: UUID | None,
        change_reason: str | None,
    ) -> None:
        profile.version += 1
        profile.last_versioned_at = datetime.now(UTC)
        self._session.flush()
        self.record_version(
            profile,
            changed_by_user_id=changed_by_user_id,
            change_reason=change_reason,
        )

    def record_version(
        self,
        profile: AgentProfile,
        *,
        changed_by_user_id: UUID | None,
        change_reason: str | None,
    ) -> AgentProfileVersion:
        version = AgentProfileVersion(
            workspace_id=profile.workspace_id,
            agent_profile_id=profile.id,
            version=profile.version,
            snapshot=profile_snapshot(profile),
            changed_by_user_id=changed_by_user_id,
            change_reason=change_reason,
        )
        self._session.add(version)
        self._session.flush()
        return version

    def audit_profile_change(
        self,
        profile: AgentProfile,
        *,
        actor_user_id: UUID | None,
        action: str,
        changed_fields: list[str],
        before_snapshot: dict[str, object] | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        if actor_user_id is None:
            return
        metadata: dict[str, object] = {
            "name": profile.name,
            "role": profile.role,
            "status": profile.status,
            "version": profile.version,
            "changed_fields": changed_fields,
            "model_provider": self._model_validator.audit_summary(
                profile.workspace_id,
                profile.model_provider_credential_id,
                profile.model_settings,
            ),
        }
        if before_snapshot is not None:
            metadata["before"] = profile_audit_state(before_snapshot)
            metadata["after"] = profile_audit_state(profile_snapshot(profile))
        if extra_metadata:
            metadata.update(extra_metadata)
        AuditService(self._session).record_user_action(
            workspace_id=profile.workspace_id,
            user_id=actor_user_id,
            action=action,
            target_type="agent_profile",
            target_id=profile.id,
            metadata=metadata,
        )
