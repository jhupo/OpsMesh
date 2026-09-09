from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.memory_policy import normalized_memory_policy
from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.marketplace import HireTalentRequest, HireTaskTalentRequest
from backend.app.audit.service import AuditService
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict
from backend.app.marketplace.listing_payloads import listing_agent_definition
from backend.app.marketplace.models import TalentListing, WorkspaceAgentInstall
from backend.app.marketplace.recommendations import (
    missing_work_package_by_id,
    string_or_default,
)
from backend.app.marketplace.talent_repository import TalentMarketplaceRepository
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage


class TalentHiringService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TalentMarketplaceRepository(session)

    def hire_for_task_staffing_gap(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        task_id: UUID,
        data: HireTaskTalentRequest,
    ) -> WorkspaceAgentInstall | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not assigned to a persistent team")
        package = missing_work_package_by_id(task, data.work_package_id)
        if package is None:
            raise ValueError("Task work package does not have a staffing gap")

        install = self.hire_agent(
            workspace_id=workspace_id,
            user_id=user_id,
            listing_id=data.listing_id,
            data=HireTalentRequest(
                agent_name=data.agent_name,
                team_id=task.agent_team_id,
                team_role=data.team_role
                or string_or_default(package.get("required_role"), "specialist"),
                order_index=data.order_index,
            ),
        )
        self._append_task_hire_message(task, install=install, package=package)
        self._session.commit()
        self._session.refresh(install)
        return install

    def hire_agent(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        listing_id: UUID,
        data: HireTalentRequest,
    ) -> WorkspaceAgentInstall:
        listing = self._session.get(TalentListing, listing_id)
        if listing is None or listing.status != "public":
            raise ValueError("Talent listing not found")
        source = self._session.get(AgentProfile, listing.source_agent_profile_id)
        if source is None or source.status != "active":
            raise ValueError("Published agent profile is not available")
        definition = listing_agent_definition(listing, source)

        installed_agent = AgentProfile(
            workspace_id=workspace_id,
            name=data.agent_name or definition.name,
            role=definition.role,
            description=definition.description,
            instructions=definition.instructions,
            model=definition.model,
            model_settings=dict(definition.model_settings),
            capabilities=dict(definition.capabilities),
            skills=dict(definition.skills),
            tool_policy=dict(definition.tool_policy),
            runtime_policy=dict(definition.runtime_policy),
            memory_policy=normalized_memory_policy(definition.memory_policy),
            approval_policy=dict(definition.approval_policy),
            version=listing.version,
        )
        self._session.add(installed_agent)
        self._session.flush()

        install = WorkspaceAgentInstall(
            workspace_id=workspace_id,
            talent_listing_id=listing.id,
            current_talent_listing_id=listing.id,
            source_agent_profile_id=source.id,
            installed_agent_profile_id=installed_agent.id,
            hired_by_user_id=user_id,
            installed_version=listing.version,
            pinned_version=True,
        )
        self._session.add(install)
        listing.install_count += 1
        if data.team_id is not None:
            self._repository.add_to_team(
                workspace_id=workspace_id,
                team_id=data.team_id,
                agent_id=installed_agent.id,
                team_role=data.team_role or listing.default_team_role or definition.role,
                order_index=data.order_index,
            )
        flush_or_raise_conflict(self._session, "Talent listing is already hired in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_listing.hired",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={
                "talent_listing_id": str(listing.id),
                "installed_agent_profile_id": str(installed_agent.id),
                "team_id": str(data.team_id) if data.team_id is not None else None,
            },
        )
        commit_or_raise_conflict(self._session, "Talent listing is already hired in workspace")
        self._session.refresh(install)
        return install

    def _append_task_hire_message(
        self,
        task: Task,
        *,
        install: WorkspaceAgentInstall,
        package: dict[str, object],
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            agent_profile_id=install.installed_agent_profile_id,
            message_type="hr.hire_confirmed",
            body=f"Hired agent for work package {package.get('package_id', 'unknown')}.",
            payload={
                "work_package_id": package.get("package_id"),
                "required_role": package.get("required_role"),
                "required_skills": package.get("required_skills", []),
                "workspace_agent_install_id": str(install.id),
                "talent_listing_id": str(install.talent_listing_id),
                "installed_agent_profile_id": str(install.installed_agent_profile_id),
            },
        )
