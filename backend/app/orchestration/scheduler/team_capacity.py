from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.policies.statuses import ACTIVE_RUN_STATUS_VALUES
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember

ACTIVE_RUN_STATUSES = ACTIVE_RUN_STATUS_VALUES


@dataclass(frozen=True)
class MemberCapacityContext:
    agent_profile_id: UUID
    team_member_id: UUID
    team_role: str
    max_concurrent_tasks: int
    active_task_ids: frozenset[UUID]


class TeamMemberCapacityResolver:
    def __init__(self, session: Session) -> None:
        self._session = session

    def contexts(self, steps: list[TaskStep]) -> dict[UUID, MemberCapacityContext | str]:
        contexts: dict[UUID, MemberCapacityContext | str] = {}
        if not steps:
            return contexts

        workspace_id = steps[0].workspace_id
        if any(step.workspace_id != workspace_id for step in steps):
            raise ValueError("Team capacity batch must belong to one workspace")
        tasks_by_id = {
            task.id: task
            for task in self._session.scalars(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.id.in_({step.task_id for step in steps}),
                )
            ).all()
        }
        active_task_ids_by_agent = self.active_task_ids_by_agent(
            workspace_id=steps[0].workspace_id,
            agent_profile_ids={
                step.assigned_agent_profile_id
                for step in steps
                if step.assigned_agent_profile_id is not None
            },
        )
        member_lookup: dict[tuple[UUID, UUID], AgentTeamMember | None] = {}
        manager_lookup: dict[UUID, AgentTeam | None] = {}
        for step in steps:
            task = tasks_by_id.get(step.task_id)
            if task is None or task.agent_team_id is None:
                continue
            self._resolve_step_context(
                step,
                task=task,
                contexts=contexts,
                member_lookup=member_lookup,
                manager_lookup=manager_lookup,
                active_task_ids_by_agent=active_task_ids_by_agent,
            )
        return contexts

    def active_task_ids_by_agent(
        self,
        *,
        workspace_id: UUID,
        agent_profile_ids: set[UUID],
    ) -> dict[UUID, set[UUID]]:
        if not agent_profile_ids:
            return {}
        result: dict[UUID, set[UUID]] = defaultdict(set)
        rows = self._session.execute(
            select(AgentRun.agent_profile_id, AgentRun.task_id).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.agent_profile_id.in_(agent_profile_ids),
                AgentRun.task_id.is_not(None),
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        ).all()
        for agent_profile_id, task_id in rows:
            if agent_profile_id is None or task_id is None:
                continue
            result[agent_profile_id].add(task_id)
        return dict(result)

    def _resolve_step_context(
        self,
        step: TaskStep,
        *,
        task: Task,
        contexts: dict[UUID, MemberCapacityContext | str],
        member_lookup: dict[tuple[UUID, UUID], AgentTeamMember | None],
        manager_lookup: dict[UUID, AgentTeam | None],
        active_task_ids_by_agent: dict[UUID, set[UUID]],
    ) -> None:
        if task.agent_team_id is None:
            return
        if step.assigned_agent_profile_id is None:
            contexts[step.id] = "team_member_unassigned"
            return

        lookup_key = (task.agent_team_id, step.assigned_agent_profile_id)
        if lookup_key not in member_lookup:
            member_lookup[lookup_key] = self._session.scalar(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == step.workspace_id,
                    AgentTeamMember.agent_team_id == task.agent_team_id,
                    AgentTeamMember.agent_profile_id == step.assigned_agent_profile_id,
                )
                .with_for_update()
            )

        member = member_lookup[lookup_key]
        if member is None:
            contexts[step.id] = self._fallback_context(
                task,
                step,
                manager_lookup=manager_lookup,
                active_task_ids_by_agent=active_task_ids_by_agent,
            )
            return
        if member.status != "active":
            contexts[step.id] = (
                snapshot_member_capacity_context(task, step, active_task_ids_by_agent)
                or "team_member_inactive"
            )
            return
        if not member.accepts_tasks:
            contexts[step.id] = "team_member_not_accepting_tasks"
            return
        contexts[step.id] = MemberCapacityContext(
            agent_profile_id=member.agent_profile_id,
            team_member_id=member.id,
            team_role=member.team_role,
            max_concurrent_tasks=max(member.max_concurrent_tasks, 1),
            active_task_ids=frozenset(active_task_ids_by_agent.get(member.agent_profile_id, set())),
        )

    def _fallback_context(
        self,
        task: Task,
        step: TaskStep,
        *,
        manager_lookup: dict[UUID, AgentTeam | None],
        active_task_ids_by_agent: dict[UUID, set[UUID]],
    ) -> MemberCapacityContext | str:
        if task.agent_team_id is None:
            return "team_member_unavailable"
        snapshot_context = snapshot_member_capacity_context(
            task,
            step,
            active_task_ids_by_agent,
        )
        if snapshot_context is not None:
            return snapshot_context
        if task.agent_team_id not in manager_lookup:
            manager_lookup[task.agent_team_id] = self._session.scalar(
                select(AgentTeam).where(
                    AgentTeam.workspace_id == step.workspace_id,
                    AgentTeam.id == task.agent_team_id,
                    AgentTeam.status == "active",
                )
            )
        manager_context = manager_capacity_context(
            manager_lookup[task.agent_team_id],
            step,
            active_task_ids_by_agent,
        )
        return manager_context or "team_member_unavailable"


def member_blocked_reason(
    step: TaskStep,
    context: MemberCapacityContext | str | None,
    selected_task_ids_by_agent: dict[UUID, set[UUID]],
) -> str | None:
    if context is None:
        return None
    if isinstance(context, str):
        return context
    if step.task_id in context.active_task_ids:
        return None
    selected_task_ids = selected_task_ids_by_agent.get(context.agent_profile_id, set())
    projected_task_count = len(context.active_task_ids | selected_task_ids | {step.task_id})
    if projected_task_count > context.max_concurrent_tasks:
        return "team_member_capacity_exceeded"
    return None


def manager_capacity_context(
    team: AgentTeam | None,
    step: TaskStep,
    active_task_ids_by_agent: dict[UUID, set[UUID]],
) -> MemberCapacityContext | None:
    if team is None or team.manager_agent_profile_id is None:
        return None
    if team.manager_agent_profile_id != step.assigned_agent_profile_id:
        return None
    if not is_manager_owned_step(step):
        return None
    return MemberCapacityContext(
        agent_profile_id=team.manager_agent_profile_id,
        team_member_id=team.id,
        team_role="project_manager",
        max_concurrent_tasks=1,
        active_task_ids=frozenset(
            active_task_ids_by_agent.get(team.manager_agent_profile_id, set())
        ),
    )


def is_manager_owned_step(step: TaskStep) -> bool:
    if step.work_package_id in {"manager-planning", "manager-summary"}:
        return True
    if isinstance(step.work_package_id, str) and step.work_package_id.startswith(
        "manager-summary-revision-"
    ):
        return True
    if step.required_role in {"project_manager", "manager", "team_lead", "lead"}:
        return True
    review_policy = step.review_policy if isinstance(step.review_policy, dict) else {}
    return review_policy.get("mode") in {"self_review", "final_acceptance"}


def snapshot_member_capacity_context(
    task: Task,
    step: TaskStep,
    active_task_ids_by_agent: dict[UUID, set[UUID]],
) -> MemberCapacityContext | None:
    if step.assigned_agent_profile_id is None:
        return None
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else {}
    raw_members = snapshot.get("members")
    if not isinstance(raw_members, list):
        return None
    expected_profile_id = str(step.assigned_agent_profile_id)
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        if str(item.get("agent_profile_id") or "") != expected_profile_id:
            continue
        if item.get("accepts_tasks") is False or item.get("status") == "inactive":
            return None
        max_concurrent_tasks = positive_int_or_default(item.get("max_concurrent_tasks"), 1)
        return MemberCapacityContext(
            agent_profile_id=step.assigned_agent_profile_id,
            team_member_id=uuid_from_snapshot(item.get("id")) or step.assigned_agent_profile_id,
            team_role=str(item.get("team_role") or step.required_role or "member"),
            max_concurrent_tasks=max(max_concurrent_tasks, 1),
            active_task_ids=frozenset(
                active_task_ids_by_agent.get(step.assigned_agent_profile_id, set())
            ),
        )
    return None


def uuid_from_snapshot(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def positive_int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default
