from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.capability_governance_rules import (
    governance_result,
    governance_skipped,
    skill_install_should_be_disabled,
)
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.capabilities.skill_tool_diagnostics import SkillToolDiagnosticsService
from backend.app.core.config import Settings, get_settings
from backend.app.observability.audit_service import AuditService


class CapabilityGovernanceSkillActionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def disable_unusable_skill_installs(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        install_ids: set[UUID],
        limit: int,
        reason: str | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        statement = (
            select(WorkspaceSkillInstall)
            .where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.status == "active",
            )
            .order_by(WorkspaceSkillInstall.installed_key.asc(), WorkspaceSkillInstall.id.asc())
        )
        if install_ids:
            statement = statement.where(WorkspaceSkillInstall.id.in_(install_ids))
        installs = self._session.scalars(statement).all()
        results: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for install in installs:
            availability = SkillToolDiagnosticsService(
                self._session,
                self._settings,
            ).workspace_skill_availability(workspace_id, install.id)
            blocked_reasons = availability.blocked_reasons
            if not skill_install_should_be_disabled(blocked_reasons):
                skipped.append(
                    governance_skipped(
                        action="disable_unusable_skill_installs",
                        resource_type="workspace_skill_install",
                        resource_id=install.id,
                        resource_name=install.installed_key,
                        reason="skill_install_not_governance_disabled",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if len(results) >= limit:
                skipped.append(
                    governance_skipped(
                        action="disable_unusable_skill_installs",
                        resource_type="workspace_skill_install",
                        resource_id=install.id,
                        resource_name=install.installed_key,
                        reason="max_items_reached",
                        blocked_reasons=blocked_reasons,
                    )
                )
                continue
            if not dry_run:
                install.status = "disabled"
                install.disabled_at = datetime.now(UTC)
                AuditService(self._session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=actor_user_id,
                    action="capability_governance.skill_install_disabled",
                    target_type="workspace_skill_install",
                    target_id=install.id,
                    metadata={
                        "installed_key": install.installed_key,
                        "blocked_reasons": blocked_reasons,
                        "reason": reason,
                    },
                )
            results.append(
                governance_result(
                    action="disable_unusable_skill_installs",
                    resource_type="workspace_skill_install",
                    resource_id=install.id,
                    resource_name=install.installed_key,
                    status="would_apply" if dry_run else "applied",
                    blocked_reasons=blocked_reasons,
                )
            )
        return results, skipped
