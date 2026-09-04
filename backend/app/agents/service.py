from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.lifecycle import (
    AGENT_STATUS_ACTIVE,
    AGENT_STATUS_ARCHIVED,
    AgentProfileLifecycleService,
)
from backend.app.agents.model_validation import AgentModelValidator
from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.agents.payloads import (
    AGENT_PROFILE_FIELDS,
    copy_json_value,
    datetime_or_none,
    normalize_update_payload,
    profile_snapshot,
    rollback_reason,
    uuid_or_none,
)
from backend.app.agents.profile_commands import AgentProfileCommandService
from backend.app.agents.queries import AgentProfileQueryService
from backend.app.agents.versions import AgentVersionRecorder
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import (
    AgentProfileCloneRequest,
    AgentProfileCreateRequest,
    AgentProfileRollbackRequest,
    AgentProfileUpdateRequest,
)
from backend.app.core.config import Settings
from backend.app.db.pagination import page_scalars


class AgentManagementService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings
        self._model_validator = AgentModelValidator(session)
        self._versions = AgentVersionRecorder(session)

    def list_agents(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentProfile], int]:
        statement = select(AgentProfile).where(AgentProfile.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        statement = statement.order_by(AgentProfile.created_at.desc(), AgentProfile.id.desc())
        return page_scalars(self._session, statement, page)

    def get_agent(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._profile(workspace_id, agent_profile_id)

    def create_agent(
        self,
        workspace_id: UUID,
        data: AgentProfileCreateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
        *,
        commit: bool = True,
    ) -> AgentProfile:
        return AgentProfileCommandService(self._session, self._settings).create_agent(
            workspace_id,
            data,
            actor_user_id,
            commit=commit,
        )

    def update_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        changes: AgentProfileUpdateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        return AgentProfileCommandService(self._session, self._settings).update_agent(
            profile,
            changes,
            actor_user_id,
        )

    def archive_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        return AgentProfileLifecycleService(self._session).archive_agent(profile, actor_user_id)

    def activate_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        return AgentProfileLifecycleService(self._session).activate_agent(profile, actor_user_id)

    def delete_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> bool:
        _ = actor_user_id
        profile = self._profile(workspace_id, agent_profile_id)
        if profile is None:
            return False
        return AgentProfileLifecycleService(self._session).delete_agent(profile)

    def clone_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        overrides: AgentProfileCloneRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        source = self._require_profile(workspace_id, agent_profile_id)
        values = {
            field: copy_json_value(field, getattr(source, field)) for field in AGENT_PROFILE_FIELDS
        }
        values.update(
            normalize_update_payload(
                overrides,
                {},
            )
        )
        self._model_validator.validate_model_provider_credential(
            workspace_id,
            values["model_provider_credential_id"],
        )
        cloned = self.create_agent(workspace_id, values, actor_user_id)
        latest_version = self._session.scalar(
            select(AgentProfileVersion).where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == cloned.id,
                AgentProfileVersion.version == 1,
            )
        )
        if latest_version is not None:
            latest_version.change_reason = f"Cloned from agent profile {source.id}"
            self._versions.audit_profile_change(
                cloned,
                actor_user_id=actor_user_id,
                action="agent.cloned",
                changed_fields=sorted(AGENT_PROFILE_FIELDS),
                extra_metadata={"source_agent_profile_id": str(source.id)},
            )
            self._session.commit()
            self._session.refresh(cloned)
        return cloned

    def rollback_agent_version(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        version: int,
        request: AgentProfileRollbackRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        historical = self._session.scalar(
            select(AgentProfileVersion).where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == agent_profile_id,
                AgentProfileVersion.version == version,
            )
        )
        if historical is None:
            raise ValueError("Agent profile version not found")

        before_snapshot = profile_snapshot(profile)
        snapshot = historical.snapshot
        values = {field: snapshot.get(field) for field in AGENT_PROFILE_FIELDS if field in snapshot}
        missing = [field for field in AGENT_PROFILE_FIELDS if field not in values]
        if missing:
            raise ValueError("Agent profile version snapshot is incomplete")
        credential_id = uuid_or_none(values["model_provider_credential_id"])
        self._model_validator.validate_model_provider_credential(workspace_id, credential_id)
        self._model_validator.validate_agent_model_api(
            workspace_id,
            credential_id,
            values["model_settings"],
        )

        for field, value in values.items():
            if field == "model_provider_credential_id":
                value = credential_id
            setattr(profile, field, copy_json_value(field, value))
        status = str(snapshot.get("status") or AGENT_STATUS_ACTIVE)
        if status not in {AGENT_STATUS_ACTIVE, AGENT_STATUS_ARCHIVED}:
            raise ValueError("Agent profile version snapshot has invalid status")
        profile.status = status
        profile.archived_at = datetime_or_none(snapshot.get("archived_at"))
        if profile.status == AGENT_STATUS_ACTIVE:
            profile.archived_at = None
        elif profile.archived_at is None:
            profile.archived_at = datetime.now(UTC)

        self._versions.bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason=rollback_reason(request) or f"Rolled back to version {version}",
        )
        self._versions.audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.rolled_back",
            changed_fields=sorted(AGENT_PROFILE_FIELDS),
            before_snapshot=before_snapshot,
            extra_metadata={"rolled_back_to_version": version},
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def list_versions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentProfileVersion]:
        return AgentProfileQueryService(self._session).list_versions(
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            limit=limit,
            offset=offset,
        )

    def list_agent_versions(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentProfileVersion], int]:
        return AgentProfileQueryService(self._session).list_agent_versions(
            workspace_id,
            agent_profile_id,
            page,
        )

    def count(self, workspace_id: UUID, *, status: str | None = None) -> int:
        return AgentProfileQueryService(self._session).count(workspace_id, status=status)

    def _require_profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile:
        return AgentProfileQueryService(self._session).require_profile(
            workspace_id,
            agent_profile_id,
        )

    def _profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return AgentProfileQueryService(self._session).profile(
            workspace_id,
            agent_profile_id,
        )
