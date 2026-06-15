from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.typing import positive_int_or_default, uuid_or_none
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot

from .team_step_project_plan import ProjectPlanStepMaterializer

STEP_STATUS_QUEUED = "queued"


@dataclass(slots=True)
class TeamStepPlanner:
    session: Session
    step_has_active_run: Callable[[TaskStep], bool] | None = None

    def create_team_step_plan(self, task: Task) -> TaskStep | None:
        if task.agent_team_id is None:
            return None
        if self._task_has_steps(task):
            next_steps = self.next_eligible_steps(task.id, task.workspace_id)
            return next_steps[0] if next_steps else None

        snapshot = self._team_snapshot_for_task(task)
        if snapshot is not None:
            return self.create_team_step_plan_from_snapshot(task, snapshot)

        return self.create_default_team_step_plan(task)

    def create_team_step_plan_from_snapshot(
        self,
        task: Task,
        snapshot: dict[str, object],
    ) -> TaskStep | None:
        project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
        if project_plan is not None:
            planned_step = self.create_team_step_plan_from_project_plan(task, project_plan)
            if planned_step is not None:
                return planned_step

        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None
        raw_members = snapshot.get("members", [])
        members = (
            [member for member in raw_members if isinstance(member, dict)]
            if isinstance(raw_members, list)
            else []
        )
        manager_agent_profile_id = uuid_or_none(team.get("manager_agent_profile_id"))
        if manager_agent_profile_id is None and not members:
            return None

        team_name = str(team.get("name") or "team")
        first_step: TaskStep | None = None
        manager_step = self._create_manager_planning_step(
            task,
            manager_agent_profile_id=manager_agent_profile_id,
        )
        if manager_step is not None:
            first_step = manager_step

        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        ordered_members = sorted(
            members,
            key=lambda member: (
                positive_int_or_default(member.get("order_index"), 0),
                str(member.get("team_role") or ""),
            ),
        )
        for index, member in enumerate(ordered_members, start=1):
            agent_profile_id = uuid_or_none(member.get("agent_profile_id"))
            if agent_profile_id is None:
                continue
            team_role = str(member.get("team_role") or "specialist")
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=agent_profile_id,
                work_package_id=f"{team_role}-{index}",
                required_role=team_role,
                required_skills=string_list_from_mapping_keys(member.get("skill_weights")),
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{team_role} execution",
                description=f"Complete the assigned team role work for {team_name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self.session.add(step)
            specialist_steps.append(step)
            if first_step is None:
                first_step = step
        self.session.flush(specialist_steps)

        if manager_agent_profile_id is not None and specialist_steps:
            self._create_manager_summary_step(
                task,
                manager_agent_profile_id=manager_agent_profile_id,
                specialist_steps=specialist_steps,
            )

        self.session.flush()
        return first_step

    def create_team_step_plan_from_project_plan(
        self,
        task: Task,
        project_plan: dict[str, object],
    ) -> TaskStep | None:
        return ProjectPlanStepMaterializer(self.session).materialize(task, project_plan)

    def create_default_team_step_plan(self, task: Task) -> TaskStep | None:
        team = self.session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )
        if team is None:
            return None

        members = self.session.scalars(
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == task.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.team_role.asc())
        ).all()
        if team.manager_agent_profile_id is None and not members:
            return None

        first_step = self._create_manager_planning_step(
            task,
            manager_agent_profile_id=team.manager_agent_profile_id,
        )
        specialist_steps = self._create_member_execution_steps(
            task,
            team=team,
            members=list(members),
            manager_step=first_step,
        )
        if first_step is None and specialist_steps:
            first_step = specialist_steps[0]
        if team.manager_agent_profile_id is not None and specialist_steps:
            self._create_manager_summary_step(
                task,
                manager_agent_profile_id=team.manager_agent_profile_id,
                specialist_steps=specialist_steps,
            )

        self.session.flush()
        return first_step

    def next_eligible_steps(self, task_id: object, workspace_id: object) -> list[TaskStep]:
        queued_steps = self.session.scalars(
            select(TaskStep)
            .where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.status == STEP_STATUS_QUEUED,
            )
            .order_by(TaskStep.order_index.asc())
        ).all()
        return [
            step
            for step in queued_steps
            if dependencies_satisfied(self.session, step) and not self._step_has_active_run(step)
        ]

    def _task_has_steps(self, task: Task) -> bool:
        return bool(
            self.session.scalar(
                select(func.count(TaskStep.id)).where(
                    TaskStep.workspace_id == task.workspace_id,
                    TaskStep.task_id == task.id,
                )
            )
        )

    def _step_has_active_run(self, step: TaskStep) -> bool:
        if self.step_has_active_run is None:
            return False
        return self.step_has_active_run(step)

    def _team_snapshot_for_task(self, task: Task) -> dict[str, object] | None:
        snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
        if snapshot is not None:
            return snapshot
        try:
            snapshot = build_team_snapshot(
                self.session,
                workspace_id=task.workspace_id,
                team_id=task.agent_team_id,
            )
        except ValueError:
            return None
        task.team_snapshot = snapshot
        self.session.flush([task])
        return snapshot

    def _create_manager_planning_step(
        self,
        task: Task,
        *,
        manager_agent_profile_id: UUID | None,
    ) -> TaskStep | None:
        if manager_agent_profile_id is None:
            return None
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=manager_agent_profile_id,
            work_package_id="manager-planning",
            required_role="project_manager",
            required_skills=["planning", "coordination"],
            expected_artifacts=["project_plan"],
            acceptance_criteria=["The team has a clear execution plan."],
            review_policy={"reviewer": "manager", "mode": "self_review"},
            title="Manager planning",
            description="Clarify the goal, split responsibilities, and prepare the team plan.",
            status=STEP_STATUS_QUEUED,
            order_index=0,
            dependencies={},
        )
        self.session.add(step)
        self.session.flush([step])
        return step

    def _create_member_execution_steps(
        self,
        task: Task,
        *,
        team: AgentTeam,
        members: list[AgentTeamMember],
        manager_step: TaskStep | None,
    ) -> list[TaskStep]:
        specialist_steps: list[TaskStep] = []
        manager_dependency = (
            {"after_step_ids": [str(manager_step.id)]} if manager_step is not None else {}
        )
        for index, member in enumerate(members, start=1):
            step = TaskStep(
                workspace_id=task.workspace_id,
                task_id=task.id,
                runtime_space_id=task.runtime_space_id,
                assigned_agent_profile_id=member.agent_profile_id,
                work_package_id=f"{member.team_role}-{index}",
                required_role=member.team_role,
                required_skills=[
                    str(skill) for skill in member.skill_weights if isinstance(skill, str)
                ],
                expected_artifacts=["work_summary"],
                acceptance_criteria=["The work package produces a clear result summary."],
                review_policy={"reviewer": "manager", "mode": "manager_review"},
                title=f"{member.team_role} execution",
                description=f"Complete the assigned team role work for {team.name}.",
                status=STEP_STATUS_QUEUED,
                order_index=100 + index,
                dependencies=manager_dependency,
            )
            self.session.add(step)
            specialist_steps.append(step)
        self.session.flush(specialist_steps)
        return specialist_steps

    def _create_manager_summary_step(
        self,
        task: Task,
        *,
        manager_agent_profile_id: UUID,
        specialist_steps: list[TaskStep],
    ) -> TaskStep:
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            runtime_space_id=task.runtime_space_id,
            assigned_agent_profile_id=manager_agent_profile_id,
            work_package_id="manager-summary",
            required_role="project_manager",
            required_skills=["review", "synthesis"],
            expected_artifacts=["final_delivery"],
            acceptance_criteria=["The final answer integrates all completed work packages."],
            review_policy={"reviewer": "user", "mode": "final_acceptance"},
            title="Manager summary",
            description=(
                "Review specialist outputs, reconcile issues, and produce the final answer."
            ),
            status=STEP_STATUS_QUEUED,
            order_index=1_000,
            dependencies={"after_step_ids": [str(step.id) for step in specialist_steps]},
        )
        self.session.add(step)
        return step

def dependencies_satisfied(session: Session, step: TaskStep) -> bool:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    raw_step_ids = dependencies.get("after_step_ids", [])
    if not isinstance(raw_step_ids, list) or not raw_step_ids:
        return True

    dependency_ids = [parsed for item in raw_step_ids if (parsed := uuid_or_none(item))]
    if len(dependency_ids) != len(raw_step_ids):
        return False
    incomplete_count = session.scalar(
        select(func.count(TaskStep.id)).where(
            TaskStep.workspace_id == step.workspace_id,
            TaskStep.id.in_(dependency_ids),
            TaskStep.status != "completed",
        )
    )
    return int(incomplete_count or 0) == 0


def string_list_from_mapping_keys(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [key for key in value if isinstance(key, str)]
