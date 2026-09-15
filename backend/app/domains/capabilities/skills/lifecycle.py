from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.common.config import Settings, get_settings
from backend.app.core.common.pagination import PageParams
from backend.app.core.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.core.db.pagination import page_scalars
from backend.app.domains.capabilities.models import Skill, WorkspaceSkillInstall
from backend.app.domains.capabilities.skills.contracts import (
    WorkspaceSkillInstallRequest,
    WorkspaceSkillRollbackRequest,
    WorkspaceSkillUpgradeRequest,
)
from backend.app.observability.audit.service import AuditService

T = TypeVar("T")


def require_workspace_install(
    session: Session,
    workspace_id: UUID,
    install_id: UUID,
) -> WorkspaceSkillInstall:
    install = session.get(WorkspaceSkillInstall, install_id)
    if install is None or install.workspace_id != workspace_id:
        raise ValueError("Workspace skill install not found")
    return install


def require_installable_skill(session: Session, workspace_id: UUID, skill_id: UUID) -> Skill:
    skill = session.get(Skill, skill_id)
    if skill is None or skill.status != "active" or not can_use_skill(workspace_id, skill):
        raise ValueError("Skill not found")
    return skill


def can_use_skill(workspace_id: UUID, skill: Skill) -> bool:
    return skill.visibility == "public" or skill.owner_workspace_id == workspace_id


def copy_skill_snapshot(install: WorkspaceSkillInstall, skill: Skill) -> None:
    install.installed_key = skill.key
    install.installed_name = skill.name
    install.installed_version = skill.version
    install.installed_description = skill.description
    install.installed_capability_keys = list(skill.capability_keys)
    install.installed_manifest = dict(skill.manifest)
    install.source_owner_workspace_id = skill.owner_workspace_id
    install.source_visibility = skill.visibility
    install.source_checksum = skill_checksum(skill)


def skill_checksum(skill: Skill) -> str:
    payload = {
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "capability_keys": skill.capability_keys,
        "manifest": skill.manifest,
        "owner_workspace_id": str(skill.owner_workspace_id)
        if skill.owner_workspace_id is not None
        else None,
        "visibility": skill.visibility,
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def require_same_skill_key(install: WorkspaceSkillInstall, skill: Skill) -> None:
    if install.installed_key != skill.key:
        raise ValueError("Skill key mismatch")


def append_skill_install_history(
    config: dict[str, object],
    install: WorkspaceSkillInstall,
    *,
    action: str,
    user_id: UUID,
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = next_config.get("_lifecycle")
    lifecycle_payload = dict(lifecycle) if isinstance(lifecycle, dict) else {}
    raw_history = lifecycle_payload.get("history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    history.append(
        {
            "skill_id": str(install.skill_id),
            "installed_key": install.installed_key,
            "installed_version": install.installed_version,
            "source_checksum": install.source_checksum,
            "status": install.status,
            "recorded_at": datetime.now(UTC).isoformat(),
            "action": action,
            "user_id": str(user_id),
        }
    )
    lifecycle_payload["history"] = history[-20:]
    next_config["_lifecycle"] = lifecycle_payload
    return next_config


def config_with_lifecycle(
    config: dict[str, object],
    lifecycle_config: dict[str, object],
) -> dict[str, object]:
    next_config = dict(config)
    lifecycle = lifecycle_config.get("_lifecycle")
    if isinstance(lifecycle, dict):
        next_config["_lifecycle"] = lifecycle
    return next_config


def latest_history_skill_id(config: dict[str, object]) -> UUID | None:
    lifecycle = config.get("_lifecycle")
    if not isinstance(lifecycle, dict):
        return None
    history = lifecycle.get("history")
    if not isinstance(history, list) or not history:
        return None
    latest = history[-1]
    if not isinstance(latest, dict):
        return None
    raw_skill_id = latest.get("skill_id")
    try:
        return UUID(str(raw_skill_id))
    except (TypeError, ValueError):
        return None


class WorkspaceSkillLifecycleService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def install_skill(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: WorkspaceSkillInstallRequest,
    ) -> WorkspaceSkillInstall:
        return self.install_skill_by_id(
            workspace_id=workspace_id,
            user_id=user_id,
            skill_id=data.skill_id,
            config=data.config,
        )

    def install_skill_by_id(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        skill_id: UUID,
        config: dict[str, object] | None = None,
    ) -> WorkspaceSkillInstall:
        skill = require_installable_skill(self._session, workspace_id, skill_id)
        install = WorkspaceSkillInstall(
            workspace_id=workspace_id,
            skill_id=skill.id,
            installed_by_user_id=user_id,
            config=config or {},
        )
        copy_skill_snapshot(install, skill)
        self._session.add(install)
        flush_or_raise_conflict(self._session, "Skill is already installed in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill.installed",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        commit_or_raise_conflict(self._session, "Skill is already installed in workspace")
        self._session.refresh(install)
        return install

    def upgrade_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        data: WorkspaceSkillUpgradeRequest,
    ) -> WorkspaceSkillInstall:
        install = require_workspace_install(self._session, workspace_id, install_id)
        skill = require_installable_skill(self._session, workspace_id, data.skill_id)
        require_same_skill_key(install, skill)
        lifecycle_config = append_skill_install_history(
            install.config,
            install,
            action="upgrade",
            user_id=user_id,
        )
        install.skill_id = skill.id
        copy_skill_snapshot(install, skill)
        install.config = (
            config_with_lifecycle(data.config, lifecycle_config)
            if data.config is not None
            else lifecycle_config
        )
        install.status = "active"
        install.disabled_at = None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.upgraded",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def rollback_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        data: WorkspaceSkillRollbackRequest,
    ) -> WorkspaceSkillInstall:
        install = require_workspace_install(self._session, workspace_id, install_id)
        target_skill_id = data.skill_id or latest_history_skill_id(install.config)
        if target_skill_id is None:
            raise ValueError("Workspace skill install has no rollback history")
        skill = require_installable_skill(self._session, workspace_id, target_skill_id)
        require_same_skill_key(install, skill)
        lifecycle_config = append_skill_install_history(
            install.config,
            install,
            action="rollback",
            user_id=user_id,
        )
        install.skill_id = skill.id
        copy_skill_snapshot(install, skill)
        install.config = (
            config_with_lifecycle(data.config, lifecycle_config)
            if data.config is not None
            else lifecycle_config
        )
        install.status = "active"
        install.disabled_at = None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.rolled_back",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def disable_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = require_workspace_install(self._session, workspace_id, install_id)
        install.status = "disabled"
        install.disabled_at = datetime.now(UTC)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.disabled",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(install.skill_id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
                "disabled_at": install.disabled_at.isoformat(),
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def list_workspace_skills(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        include_disabled: bool = False,
    ) -> tuple[list[WorkspaceSkillInstall], int]:
        statement = select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
        )
        if not include_disabled:
            statement = statement.where(WorkspaceSkillInstall.status == "active")
        statement = statement.order_by(
            WorkspaceSkillInstall.created_at.desc(),
            WorkspaceSkillInstall.id.desc(),
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)
