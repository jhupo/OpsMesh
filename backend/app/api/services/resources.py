from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import AgentProfileCreateRequest
from backend.app.api.schemas.tasks import (
    TaskCreateRequest,
    TaskPlanRegenerateRequest,
    TaskPlanRetryRequest,
)
from backend.app.api.schemas.teams import (
    AgentTeamCreateRequest,
    AgentTeamMemberCreateRequest,
    AgentTeamMemberUpdateRequest,
)
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.attempts import TaskPlanningAttemptService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot
from backend.app.workers.queue import RedisQueue

T = TypeVar("T")


class WorkspaceResourceService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def list_agents(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentProfile], int]:
        statement = select(AgentProfile).where(AgentProfile.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        return self._page(statement.order_by(AgentProfile.created_at.desc()), page)

    def create_agent(
        self,
        workspace_id: UUID,
        data: AgentProfileCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        if data.model_provider_credential_id is not None:
            self._require_model_provider_credential(
                workspace_id,
                data.model_provider_credential_id,
            )
        agent = AgentProfile(workspace_id=workspace_id, **data.model_dump())
        self._session.add(agent)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="agent.created",
                target_type="agent_profile",
                target_id=agent.id,
                metadata={"name": agent.name, "role": agent.role},
            )
        self._session.commit()
        self._session.refresh(agent)
        return agent

    def get_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile | None:
        return self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_id,
            )
        )

    def _require_model_provider_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> None:
        credential = self._session.scalar(
            select(ModelProviderCredential.id).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError("Model provider credential not found")

    def list_teams(self, workspace_id: UUID, page: PageParams) -> tuple[list[AgentTeam], int]:
        statement = (
            select(AgentTeam)
            .where(AgentTeam.workspace_id == workspace_id)
            .order_by(AgentTeam.created_at.desc())
        )
        return self._page(statement, page)

    def get_team_org_chart(
        self,
        workspace_id: UUID,
        team_id: UUID,
    ) -> dict[str, object] | None:
        team = self.get_team(workspace_id, team_id)
        if team is None:
            return None

        members = self._session.scalars(
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
        ).all()
        agent_ids = {member.agent_profile_id for member in members}
        if team.manager_agent_profile_id is not None:
            agent_ids.add(team.manager_agent_profile_id)
        agents = {
            agent.id: agent
            for agent in self._session.scalars(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id.in_(agent_ids),
                )
            ).all()
        }
        member_nodes = [
            _team_org_member_node(member, agents.get(member.agent_profile_id))
            for member in members
        ]
        node_by_id = {str(node["id"]): node for node in member_nodes}
        reports_to_by_id = {
            str(member.id): str(member.reports_to_member_id)
            for member in members
            if member.reports_to_member_id is not None
        }
        roots: list[dict[str, object]] = []
        orphan_member_ids: list[UUID] = []
        cycle_member_ids: list[UUID] = []
        for node in member_nodes:
            reports_to_member_id = node["reports_to_member_id"]
            parent = (
                node_by_id.get(str(reports_to_member_id))
                if reports_to_member_id is not None
                else None
            )
            if reports_to_member_id is not None and _has_reporting_cycle(
                str(node["id"]),
                reports_to_by_id,
            ):
                roots.append(node)
                cycle_member_ids.append(node["id"])
                continue
            if parent is None:
                roots.append(node)
                if reports_to_member_id is not None:
                    orphan_member_ids.append(node["id"])
                continue
            parent["children"].append(node)

        return {
            "workspace_id": team.workspace_id,
            "team_id": team.id,
            "name": team.name,
            "team_type": team.team_type,
            "description": team.description,
            "status": team.status,
            "manager_agent_profile_id": team.manager_agent_profile_id,
            "manager_agent": _team_org_agent_summary(
                agents.get(team.manager_agent_profile_id)
            )
            if team.manager_agent_profile_id is not None
            else None,
            "runtime_space_id": team.runtime_space_id,
            "coordination_rules": team.coordination_rules,
            "default_task_policy": team.default_task_policy,
            "roots": roots,
            "members": member_nodes,
            "orphan_member_ids": orphan_member_ids,
            "cycle_member_ids": cycle_member_ids,
            "capacity_summary": _team_capacity_summary(members),
        }

    def create_team(
        self,
        workspace_id: UUID,
        data: AgentTeamCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentTeam:
        if data.runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space(
                workspace_id,
                data.runtime_space_id,
            )
        if data.manager_agent_profile_id is not None:
            self._require_agent(workspace_id, data.manager_agent_profile_id)
        team = AgentTeam(workspace_id=workspace_id, **data.model_dump())
        self._session.add(team)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.created",
                target_type="agent_team",
                target_id=team.id,
                metadata={"name": team.name, "team_type": team.team_type},
            )
        self._session.commit()
        self._session.refresh(team)
        return team

    def get_team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
        )

    def list_team_members(
        self,
        workspace_id: UUID,
        team_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentTeamMember], int]:
        self._require_team(workspace_id, team_id)
        statement = (
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
        )
        return self._page(statement, page)

    def get_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        member_id: UUID,
    ) -> AgentTeamMember | None:
        return self._session.scalar(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.id == member_id,
            )
        )

    def create_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        data: AgentTeamMemberCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentTeamMember:
        self._require_team(workspace_id, team_id)
        self._require_agent(workspace_id, data.agent_profile_id)
        if data.reports_to_member_id is not None:
            self._require_team_member(workspace_id, team_id, data.reports_to_member_id)

        member = AgentTeamMember(
            workspace_id=workspace_id,
            agent_team_id=team_id,
            **data.model_dump(),
        )
        self._session.add(member)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team_member.created",
                target_type="agent_team_member",
                target_id=member.id,
                metadata={
                    "agent_team_id": str(team_id),
                    "agent_profile_id": str(member.agent_profile_id),
                    "team_role": member.team_role,
                    "department": member.department,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def update_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        member_id: UUID,
        data: AgentTeamMemberUpdateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentTeamMember:
        member = self.get_team_member(workspace_id, team_id, member_id)
        if member is None:
            raise ValueError("Team member not found")
        if data.reports_to_member_id is not None:
            if data.reports_to_member_id == member.id:
                raise ValueError("Team member cannot report to itself")
            self._require_team_member(workspace_id, team_id, data.reports_to_member_id)

        changes = data.model_dump(exclude_unset=True)
        before = _team_member_update_snapshot(member, changes)
        for field, value in changes.items():
            setattr(member, field, value)
        self._session.flush([member])
        if actor_user_id is not None and changes:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team_member.updated",
                target_type="agent_team_member",
                target_id=member.id,
                metadata={
                    "agent_team_id": str(team_id),
                    "agent_profile_id": str(member.agent_profile_id),
                    "changed_fields": sorted(changes),
                    "before": before,
                    "after": _team_member_update_snapshot(member, changes),
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def list_tasks(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[Task], int]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        return self._page(statement.order_by(Task.created_at.desc()), page)

    def create_task(
        self,
        workspace_id: UUID,
        created_by_user_id: UUID,
        data: TaskCreateRequest,
        *,
        queue: RedisQueue | None = None,
    ) -> Task:
        payload = data.model_dump()
        if data.runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space(
                workspace_id,
                data.runtime_space_id,
            )
        if data.agent_team_id is not None:
            team = self._require_team(workspace_id, data.agent_team_id)
            if payload.get("runtime_space_id") is None:
                payload["runtime_space_id"] = team.runtime_space_id
            payload["team_snapshot"] = build_team_snapshot(
                self._session,
                workspace_id=workspace_id,
                team_id=data.agent_team_id,
            )
        task = Task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            **payload,
        )
        self._session.add(task)
        self._session.flush()
        if task.agent_team_id is not None:
            TaskPlanningAttemptService(self._session).ensure_initial_plan(task)
        initial_run = None
        initial_run_enqueued = False
        if task.project_plan is not None or task.agent_team_id is None:
            orchestration = RunOrchestrationService(
                self._session,
                queue=queue,
                settings=self._settings,
            )
            initial_run = orchestration.create_queued_run_for_task(task)
            if initial_run is not None and queue is not None:
                initial_run_enqueued = orchestration.enqueue_run(
                    initial_run,
                    created_by_user_id,
                )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            action="task.created",
            target_type="task",
            target_id=task.id,
            metadata={
                "title": task.title,
                "domain_type": task.domain_type,
                "initial_run_id": str(initial_run.id) if initial_run is not None else None,
                "initial_run_enqueued": initial_run_enqueued,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def retry_task_plan(
        self,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        data: TaskPlanRetryRequest,
        *,
        enqueue_run: bool = False,
        queue: RedisQueue | None = None,
    ) -> Task | None:
        task = self.get_task(workspace_id, task_id)
        if task is None:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not team-backed")
        if data.input is not None:
            task.input = data.input
        if data.refresh_team_snapshot:
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=workspace_id,
                team_id=task.agent_team_id,
            )
        if task.team_snapshot is None:
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=workspace_id,
                team_id=task.agent_team_id,
            )
        task.project_plan = None
        if task.status in {"blocked", "failed"}:
            task.status = "draft"
        TaskPlanningAttemptService(self._session).ensure_initial_plan(
            task,
            transition_to_planning=task.status in {"draft", "queued", "blocked", "failed"},
        )
        run = None
        if task.project_plan is not None:
            run = RunOrchestrationService(
                self._session,
                settings=self._settings,
            ).create_queued_run_for_task(task)
            if enqueue_run and run is not None:
                RunOrchestrationService(
                    self._session,
                    queue=queue,
                ).enqueue_run(run, actor_user_id)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.plan_retried",
            target_type="task",
            target_id=task.id,
            metadata={
                "refresh_team_snapshot": data.refresh_team_snapshot,
                "input_replaced": data.input is not None,
                "planned": task.project_plan is not None,
                "run_id": str(run.id) if run is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def regenerate_task_plan(
        self,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        data: TaskPlanRegenerateRequest,
        *,
        enqueue_run: bool = False,
        queue: RedisQueue | None = None,
    ) -> Task | None:
        task = self.get_task(workspace_id, task_id)
        if task is None:
            return None
        if task.agent_team_id is None:
            raise ValueError("Task is not team-backed")
        completed_work_package_ids = self._completed_work_package_ids(workspace_id, task.id)
        if data.input is not None:
            task.input = data.input
        if data.refresh_team_snapshot or task.team_snapshot is None:
            task.team_snapshot = build_team_snapshot(
                self._session,
                workspace_id=workspace_id,
                team_id=task.agent_team_id,
            )
        previous_plan = task.project_plan
        task.project_plan = None
        if task.status in {"blocked", "failed"}:
            task.status = "draft"
        TaskPlanningAttemptService(self._session).ensure_initial_plan(task)
        if task.project_plan is not None:
            task.project_plan = {
                **task.project_plan,
                "regeneration": {
                    "mode": "future_only",
                    "previous_plan_id": previous_plan.get("plan_id")
                    if isinstance(previous_plan, dict)
                    else None,
                    "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                    "regenerated_by_user_id": str(actor_user_id),
                },
            }
            self._sync_latest_planning_attempt_output(workspace_id, task.id, task.project_plan)
        run = None
        if task.project_plan is not None and not completed_work_package_ids:
            run = RunOrchestrationService(
                self._session,
                settings=self._settings,
            ).create_queued_run_for_task(task)
            if enqueue_run and run is not None:
                RunOrchestrationService(self._session, queue=queue).enqueue_run(
                    run,
                    actor_user_id,
                )
        self._append_task_message(
            task,
            "planning.regenerated",
            {
                "mode": "future_only",
                "planned": task.project_plan is not None,
                "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                "run_id": str(run.id) if run is not None else None,
            },
        )
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="task.plan_regenerated",
            target_type="task",
            target_id=task.id,
            metadata={
                "refresh_team_snapshot": data.refresh_team_snapshot,
                "input_replaced": data.input is not None,
                "preserved_completed_work_package_ids": sorted(completed_work_package_ids),
                "run_id": str(run.id) if run is not None else None,
            },
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def get_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

    def list_task_messages(
        self,
        workspace_id: UUID,
        task_id: UUID,
        page: PageParams,
        message_type: str | None = None,
    ) -> tuple[list[TaskMessage], int]:
        self._require_task(workspace_id, task_id)
        statement = select(TaskMessage).where(
            TaskMessage.workspace_id == workspace_id,
            TaskMessage.task_id == task_id,
        )
        if message_type is not None:
            statement = statement.where(TaskMessage.message_type == message_type)
        return self._page(statement.order_by(TaskMessage.sequence.asc()), page)

    def list_task_planning_attempts(
        self,
        workspace_id: UUID,
        task_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[TaskPlanningAttempt], int]:
        self._require_task(workspace_id, task_id)
        statement = select(TaskPlanningAttempt).where(
            TaskPlanningAttempt.workspace_id == workspace_id,
            TaskPlanningAttempt.task_id == task_id,
        )
        if status is not None:
            statement = statement.where(TaskPlanningAttempt.status == status)
        return self._page(statement.order_by(TaskPlanningAttempt.attempt_number.desc()), page)

    def list_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentRun], int]:
        statement = select(AgentRun).where(AgentRun.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return self._page(statement.order_by(AgentRun.created_at.desc()), page)

    def list_run_events(
        self,
        workspace_id: UUID,
        agent_run_id: UUID,
        page: PageParams,
    ) -> tuple[list[RunEvent], int]:
        statement = (
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id == agent_run_id)
            .order_by(RunEvent.sequence.asc())
        )
        return self._page(statement, page)

    def list_audit_events(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AuditEvent], int]:
        statement = (
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.desc())
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def _require_team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam:
        team = self.get_team(workspace_id, team_id)
        if team is None:
            raise ValueError("Team not found")
        return team

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> None:
        if self.get_agent(workspace_id, agent_id) is None:
            raise ValueError("Agent not found")

    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None:
        if self.get_task(workspace_id, task_id) is None:
            raise ValueError("Task not found")

    def _completed_work_package_ids(self, workspace_id: UUID, task_id: UUID) -> set[str]:
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == "completed",
                TaskStep.work_package_id.is_not(None),
            )
        ).all()
        return {str(step.work_package_id) for step in steps if step.work_package_id}

    def _sync_latest_planning_attempt_output(
        self,
        workspace_id: UUID,
        task_id: UUID,
        project_plan: dict[str, object],
    ) -> None:
        attempt = self._session.scalar(
            select(TaskPlanningAttempt)
            .where(
                TaskPlanningAttempt.workspace_id == workspace_id,
                TaskPlanningAttempt.task_id == task_id,
                TaskPlanningAttempt.status == "completed",
            )
            .order_by(TaskPlanningAttempt.attempt_number.desc())
        )
        if attempt is not None:
            attempt.output_snapshot = project_plan
            self._session.flush([attempt])

    def _append_task_message(
        self,
        task: Task,
        message_type: str,
        payload: dict[str, object],
    ) -> TaskMessage:
        next_sequence = (
            self._session.scalar(
                select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                    TaskMessage.workspace_id == task.workspace_id,
                    TaskMessage.task_id == task.id,
                )
            )
            or 0
        ) + 1
        message = TaskMessage(
            workspace_id=task.workspace_id,
            task_id=task.id,
            message_type=message_type,
            sequence=next_sequence,
            body="Project plan regenerated for future work.",
            payload=payload,
        )
        self._session.add(message)
        self._session.flush([message])
        return message

    def _require_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        team_member_id: UUID,
    ) -> None:
        member = self._session.scalar(
            select(AgentTeamMember.id).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.id == team_member_id,
            )
        )
        if member is None:
            raise ValueError("Reporting manager team member not found")


def _team_member_update_snapshot(
    member: AgentTeamMember,
    fields: dict[str, object],
) -> dict[str, object]:
    return {
        field: _serializable_team_member_value(getattr(member, field))
        for field in fields
    }


def _serializable_team_member_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    return value


def _team_org_member_node(
    member: AgentTeamMember,
    agent: AgentProfile | None,
) -> dict[str, object]:
    return {
        "id": member.id,
        "agent_profile_id": member.agent_profile_id,
        "reports_to_member_id": member.reports_to_member_id,
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": member.responsibilities,
        "skill_weights": member.skill_weights,
        "availability": member.availability,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "order_index": member.order_index,
        "status": member.status,
        "agent": _team_org_agent_summary(agent),
        "children": [],
    }


def _team_org_agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _team_capacity_summary(members: list[AgentTeamMember]) -> dict[str, int]:
    return {
        "total_members": len(members),
        "active_members": sum(1 for member in members if member.status == "active"),
        "accepting_members": sum(
            1
            for member in members
            if member.status == "active" and member.accepts_tasks
        ),
        "required_members": sum(
            1
            for member in members
            if member.status == "active" and member.is_required
        ),
        "inactive_members": sum(1 for member in members if member.status != "active"),
        "total_max_concurrent_tasks": sum(
            member.max_concurrent_tasks
            for member in members
            if member.status == "active" and member.accepts_tasks
        ),
    }


def _has_reporting_cycle(
    member_id: str,
    reports_to_by_id: dict[str, str],
) -> bool:
    seen: set[str] = set()
    current = member_id
    while current in reports_to_by_id:
        if current in seen:
            return True
        seen.add(current)
        current = reports_to_by_id[current]
    return False
