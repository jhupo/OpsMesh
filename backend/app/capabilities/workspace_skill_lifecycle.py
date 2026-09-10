from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.capabilities.workspace_skills import (
    WorkspaceSkillInstallRequest,
    WorkspaceSkillRollbackRequest,
    WorkspaceSkillUpgradeRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.capabilities.workspace_skill_lifecycle_helpers import (
    append_skill_install_history,
    config_with_lifecycle,
    copy_skill_snapshot,
    latest_history_skill_id,
    require_installable_skill,
    require_same_skill_key,
    require_workspace_install,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.db.pagination import page_scalars

T = TypeVar("T")


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
