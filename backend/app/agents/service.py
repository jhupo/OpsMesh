from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import (
    AgentProfileCloneRequest,
    AgentProfileCreateRequest,
    AgentProfileRollbackRequest,
    AgentProfileUpdateRequest,
)
from backend.app.audit.service import AuditService
from backend.app.model_providers.model_api import (
    canonical_model_api,
    default_model_api,
    model_api_for_agent_provider,
    model_api_options_for_provider,
    require_known_model_api,
    unsupported_agent_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential

AGENT_STATUS_ACTIVE = "active"
AGENT_STATUS_ARCHIVED = "archived"

AGENT_PROFILE_FIELDS = (
    "name",
    "role",
    "description",
    "instructions",
    "model",
    "model_provider_credential_id",
    "model_settings",
    "capabilities",
    "skills",
    "tool_policy",
    "runtime_policy",
    "memory_policy",
    "approval_policy",
)

JSON_PROFILE_FIELDS = (
    "model_settings",
    "capabilities",
    "skills",
    "tool_policy",
    "runtime_policy",
    "memory_policy",
    "approval_policy",
)

NON_NULL_PROFILE_FIELDS = (
    "name",
    "role",
    "description",
    "instructions",
    "model",
)

CREATE_DEFAULTS: dict[str, object] = {
    "description": "",
    "instructions": "",
    "model": "gpt-4.1",
    "model_provider_credential_id": None,
    "model_settings": {},
    "capabilities": {},
    "skills": {},
    "tool_policy": {},
    "runtime_policy": {},
    "memory_policy": {},
    "approval_policy": {},
}


class AgentManagementService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_agents(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentProfile], int]:
        statement = select(AgentProfile).where(AgentProfile.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        total = self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        statement = statement.order_by(AgentProfile.created_at.desc(), AgentProfile.id.desc())
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), total

    def get_agent(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._profile(workspace_id, agent_profile_id)

    def create_agent(
        self,
        workspace_id: UUID,
        data: AgentProfileCreateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        values = self._normalize_create_payload(data, {})
        self._validate_model_provider_credential(
            workspace_id,
            values["model_provider_credential_id"],
        )

        now = datetime.now(UTC)
        profile = AgentProfile(
            workspace_id=workspace_id,
            **values,
            status=AGENT_STATUS_ACTIVE,
            version=1,
            archived_at=None,
            last_versioned_at=now,
        )
        self._session.add(profile)
        self._session.flush()
        self._record_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile created",
        )
        self._audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.created",
            changed_fields=sorted(AGENT_PROFILE_FIELDS),
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def update_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        changes: AgentProfileUpdateRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        values = self._normalize_update_payload(
            changes,
            {},
            current_model_settings=profile.model_settings,
        )
        if not values:
            return profile
        if "status" in values:
            raise ValueError("Use archive or activate to change agent lifecycle status")
        before_snapshot = _profile_snapshot(profile)
        if "model_provider_credential_id" in values:
            self._validate_model_provider_credential(
                workspace_id,
                values["model_provider_credential_id"],
            )

        for field, value in values.items():
            setattr(profile, field, self._copy_json_value(field, value))
        self._bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile updated",
        )
        self._audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.updated",
            changed_fields=sorted(values),
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

    def archive_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        if profile.status == AGENT_STATUS_ARCHIVED:
            return profile
        before_snapshot = _profile_snapshot(profile)
        profile.status = AGENT_STATUS_ARCHIVED
        profile.archived_at = datetime.now(UTC)
        self._bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile archived",
        )
        self._audit_profile_change(
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
        workspace_id: UUID,
        agent_profile_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile | None:
        profile = self._require_profile(workspace_id, agent_profile_id)
        if profile.status == AGENT_STATUS_ACTIVE:
            return profile
        before_snapshot = _profile_snapshot(profile)
        profile.status = AGENT_STATUS_ACTIVE
        profile.archived_at = None
        self._bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason="Agent profile activated",
        )
        self._audit_profile_change(
            profile,
            actor_user_id=actor_user_id,
            action="agent.activated",
            changed_fields=["archived_at", "status"],
            before_snapshot=before_snapshot,
        )
        self._session.commit()
        self._session.refresh(profile)
        return profile

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
        self._session.delete(profile)
        self._session.commit()
        return True

    def clone_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        overrides: AgentProfileCloneRequest | Mapping[str, Any],
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        source = self._require_profile(workspace_id, agent_profile_id)
        values = {
            field: self._copy_json_value(field, getattr(source, field))
            for field in AGENT_PROFILE_FIELDS
        }
        values.update(
            self._normalize_update_payload(
                overrides,
                {},
                current_model_settings=source.model_settings,
            )
        )
        self._validate_model_provider_credential(
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
            self._audit_profile_change(
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

        before_snapshot = _profile_snapshot(profile)
        snapshot = historical.snapshot
        values = {
            field: snapshot.get(field)
            for field in AGENT_PROFILE_FIELDS
            if field in snapshot
        }
        missing = [field for field in AGENT_PROFILE_FIELDS if field not in values]
        if missing:
            raise ValueError("Agent profile version snapshot is incomplete")
        credential_id = _uuid_or_none(values["model_provider_credential_id"])
        self._validate_model_provider_credential(workspace_id, credential_id)

        for field, value in values.items():
            if field == "model_provider_credential_id":
                value = credential_id
            setattr(profile, field, self._copy_json_value(field, value))
        status = str(snapshot.get("status") or AGENT_STATUS_ACTIVE)
        if status not in {AGENT_STATUS_ACTIVE, AGENT_STATUS_ARCHIVED}:
            raise ValueError("Agent profile version snapshot has invalid status")
        profile.status = status
        profile.archived_at = _datetime_or_none(snapshot.get("archived_at"))
        if profile.status == AGENT_STATUS_ACTIVE:
            profile.archived_at = None
        elif profile.archived_at is None:
            profile.archived_at = datetime.now(UTC)

        self._bump_version(
            profile,
            changed_by_user_id=actor_user_id,
            change_reason=_rollback_reason(request) or f"Rolled back to version {version}",
        )
        self._audit_profile_change(
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
        self._require_profile(workspace_id, agent_profile_id)
        statement = (
            select(AgentProfileVersion)
            .where(
                AgentProfileVersion.workspace_id == workspace_id,
                AgentProfileVersion.agent_profile_id == agent_profile_id,
            )
            .order_by(AgentProfileVersion.version.desc())
        )
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self._session.scalars(statement).all())

    def list_agent_versions(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentProfileVersion], int]:
        self._require_profile(workspace_id, agent_profile_id)
        statement = select(AgentProfileVersion).where(
            AgentProfileVersion.workspace_id == workspace_id,
            AgentProfileVersion.agent_profile_id == agent_profile_id,
        )
        total = self._session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = self._session.scalars(
            statement.order_by(AgentProfileVersion.version.desc())
            .limit(page.limit)
            .offset(page.offset)
        ).all()
        return list(rows), total

    def count(self, workspace_id: UUID, *, status: str | None = None) -> int:
        statement = select(func.count()).select_from(AgentProfile).where(
            AgentProfile.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        return int(self._session.scalar(statement) or 0)

    def _require_profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile:
        profile = self._profile(workspace_id, agent_profile_id)
        if profile is None:
            raise ValueError("Agent profile not found")
        return profile

    def _profile(self, workspace_id: UUID, agent_profile_id: UUID) -> AgentProfile | None:
        return self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
            )
        )

    def _validate_model_provider_credential(
        self,
        workspace_id: UUID,
        credential_id: object,
    ) -> None:
        credential_uuid = _uuid_or_none(credential_id)
        if credential_uuid is None:
            return
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_uuid,
            )
        )
        if credential is None:
            raise ValueError("Model provider credential not found")
        if credential.status != "active":
            raise ValueError("Model provider credential is not active")

    def _normalize_create_payload(
        self,
        data: object,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        values = dict(CREATE_DEFAULTS)
        payload = _payload_dict(data)
        payload.update(fields)
        payload.pop("status", None)
        _merge_top_level_model_api(payload)
        unknown = set(payload) - set(AGENT_PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported agent profile fields: {', '.join(sorted(unknown))}")
        missing_required = [field for field in ("name", "role") if not payload.get(field)]
        if missing_required:
            raise ValueError(f"Missing agent profile fields: {', '.join(missing_required)}")
        _reject_null_profile_fields(payload)
        values.update(payload)
        return {
            field: self._copy_json_value(field, values[field])
            for field in AGENT_PROFILE_FIELDS
        }

    def _normalize_update_payload(
        self,
        data: object,
        fields: Mapping[str, Any],
        *,
        current_model_settings: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        payload = _payload_dict(data)
        payload.update(fields)
        _merge_top_level_model_api(payload, current_model_settings=current_model_settings)
        unknown = set(payload) - (set(AGENT_PROFILE_FIELDS) | {"status"})
        if unknown:
            raise ValueError(f"Unsupported agent profile fields: {', '.join(sorted(unknown))}")
        _reject_null_profile_fields(payload)
        for required_text_field in ("name", "role"):
            if required_text_field in payload and not payload[required_text_field]:
                raise ValueError(f"Agent profile {required_text_field} is required")
        return {
            field: self._copy_json_value(field, value)
            for field, value in payload.items()
        }

    def _bump_version(
        self,
        profile: AgentProfile,
        *,
        changed_by_user_id: UUID | None,
        change_reason: str | None,
    ) -> None:
        profile.version += 1
        profile.last_versioned_at = datetime.now(UTC)
        self._session.flush()
        self._record_version(
            profile,
            changed_by_user_id=changed_by_user_id,
            change_reason=change_reason,
        )

    def _record_version(
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
            snapshot=_profile_snapshot(profile),
            changed_by_user_id=changed_by_user_id,
            change_reason=change_reason,
        )
        self._session.add(version)
        self._session.flush()
        return version

    def _audit_profile_change(
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
            "model_provider": _model_provider_audit_summary(
                self._session,
                profile.workspace_id,
                profile.model_provider_credential_id,
                profile.model_settings,
            ),
        }
        if before_snapshot is not None:
            metadata["before"] = _profile_audit_state(before_snapshot)
            metadata["after"] = _profile_audit_state(_profile_snapshot(profile))
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

    @staticmethod
    def _copy_json_value(field: str, value: Any) -> Any:
        if field in JSON_PROFILE_FIELDS:
            if value is None:
                return {}
            if not isinstance(value, Mapping):
                raise ValueError(f"Agent profile {field} must be an object")
            return dict(value)
        if field == "model_provider_credential_id":
            return _uuid_or_none(value)
        return value


def _payload_dict(data: object) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, Mapping):
        return dict(data)
    model_dump = getattr(data, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(exclude_unset=True))
    raise ValueError("Agent profile payload must be a mapping")


def _reject_null_profile_fields(payload: Mapping[str, Any]) -> None:
    null_fields = [
        field
        for field in NON_NULL_PROFILE_FIELDS
        if field in payload and payload[field] is None
    ]
    if null_fields:
        raise ValueError(f"Agent profile fields cannot be null: {', '.join(null_fields)}")


def _rollback_reason(data: object) -> str | None:
    payload = _payload_dict(data)
    reason = payload.get("reason")
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError("Rollback reason must be a string")
    return reason


def _profile_snapshot(profile: AgentProfile) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "id": str(profile.id),
        "workspace_id": str(profile.workspace_id),
        "version": profile.version,
        "status": profile.status,
        "archived_at": profile.archived_at.isoformat() if profile.archived_at else None,
    }
    for field in AGENT_PROFILE_FIELDS:
        value = getattr(profile, field)
        if isinstance(value, UUID):
            snapshot[field] = str(value)
        elif field in JSON_PROFILE_FIELDS:
            snapshot[field] = dict(value or {})
        else:
            snapshot[field] = value
    return snapshot


def _profile_audit_state(snapshot: Mapping[str, object]) -> dict[str, object]:
    return {
        "status": snapshot.get("status"),
        "version": snapshot.get("version"),
        "model": snapshot.get("model"),
        "model_provider_credential_id": snapshot.get("model_provider_credential_id"),
    }


def _merge_top_level_model_api(
    payload: dict[str, Any],
    *,
    current_model_settings: Mapping[str, object] | None = None,
) -> None:
    if "model_api" not in payload:
        return
    raw_model_api = payload.pop("model_api")
    raw_settings = payload.get("model_settings")
    if raw_settings is None:
        raw_settings = current_model_settings
    if raw_settings is not None and not isinstance(raw_settings, Mapping):
        raise ValueError("Agent profile model_settings must be an object")
    settings = dict(raw_settings or {})
    model_api = require_known_model_api(raw_model_api)
    if model_api is None:
        settings.pop("model_api", None)
    else:
        settings["model_api"] = model_api
    payload["model_settings"] = settings


def _model_provider_audit_summary(
    session: Session,
    workspace_id: UUID,
    credential_id: UUID | None,
    model_settings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if credential_id is None:
        agent_model_api = _configured_model_api(model_settings)
        return {
            "credential_id": None,
            "provider": None,
            "default_model": None,
            "model_api": agent_model_api,
            "model_apis": [],
            "default_model_api": None,
            "credential_status": None,
            "credential_health_status": None,
        }
    credential = session.scalar(
        select(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id,
            ModelProviderCredential.id == credential_id,
        )
    )
    if credential is None:
        return {
            "credential_id": str(credential_id),
            "provider": None,
            "default_model": None,
            "model_api": None,
            "model_apis": [],
            "default_model_api": None,
            "credential_status": "missing",
            "credential_health_status": None,
        }
    unsupported_model_api = unsupported_agent_model_api(
        credential.provider,
        dict(model_settings or {}),
    )
    payload = {
        "credential_id": str(credential.id),
        "provider": credential.provider,
        "default_model": credential.default_model,
        "model_api": model_api_for_agent_provider(
            credential.provider,
            dict(model_settings or {}),
            credential.budget_metadata,
        ),
        "model_apis": list(model_api_options_for_provider(credential.provider)),
        "default_model_api": default_model_api(credential.provider),
        "credential_status": credential.status,
        "credential_health_status": credential.health_status,
        "is_default": credential.is_default,
    }
    if unsupported_model_api is not None:
        payload["requested_model_api"] = unsupported_model_api
    return payload


def _configured_model_api(model_settings: Mapping[str, object] | None) -> str | None:
    if model_settings is None:
        return None
    return canonical_model_api(model_settings.get("model_api"))


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("Expected UUID value")


def _datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Expected datetime value")
