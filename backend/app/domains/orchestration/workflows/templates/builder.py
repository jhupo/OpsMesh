from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowNode
from backend.app.domains.orchestration.workflows.definitions.graph import validate_project_plan
from backend.app.domains.orchestration.workflows.planning.member_matching import (
    MemberMatchingService,
)
from backend.app.domains.orchestration.workflows.templates.execution import (
    ExecutionPackageAppender,
)
from backend.app.domains.orchestration.workflows.templates.leadership import (
    LeadershipPackageAppender,
)
from backend.app.domains.orchestration.workflows.templates.members import (
    execution_members,
    executive_members,
    first_member_agent_profile_id,
    lead_members,
    manager_member,
    member_agent_profile_id,
    member_agent_profile_ids,
    snapshot_members,
)
from backend.app.domains.orchestration.workflows.templates.normalization import (
    uuid_or_none,
)


class PlanningContext:
    def __init__(
        self,
        *,
        snapshot: dict[str, object],
        members: list[dict[str, object]],
        executives: list[dict[str, object]],
        manager: dict[str, object] | None,
        manager_planner_id: UUID | None,
        leads: list[dict[str, object]],
        execution_members_: list[dict[str, object]],
    ) -> None:
        self.snapshot = snapshot
        self.members = members
        self.executives = executives
        self.manager = manager
        self.manager_planner_id = manager_planner_id
        self.leads = leads
        self.execution_members = execution_members_

    @classmethod
    def from_task(cls, task: Task) -> PlanningContext | None:
        if not isinstance(task.team_snapshot, dict):
            return None
        snapshot = task.team_snapshot
        team = snapshot.get("team")
        if not isinstance(team, dict):
            return None

        manager_agent_profile_id = uuid_or_none(team.get("manager_agent_profile_id"))
        members = snapshot_members(snapshot)
        if manager_agent_profile_id is None and not members:
            return None

        executives = executive_members(members)
        manager = manager_member(members, manager_agent_profile_id)
        executive_agent_ids = member_agent_profile_ids(executives)
        manager_planner_id = member_agent_profile_id(manager) or (
            manager_agent_profile_id
            if manager_agent_profile_id not in executive_agent_ids
            else None
        )
        leads = lead_members(members, manager_planner_id)
        return cls(
            snapshot=snapshot,
            members=members,
            executives=executives,
            manager=manager,
            manager_planner_id=manager_planner_id,
            leads=leads,
            execution_members_=execution_members(
                members,
                executives=executives,
                manager=manager,
                manager_agent_profile_id=manager_planner_id,
                leads=leads,
            ),
        )

    @property
    def planner_agent_profile_id(self) -> UUID | None:
        return self.manager_planner_id or first_member_agent_profile_id(
            self.executives or self.leads or self.execution_members,
        )


@dataclass(frozen=True)
class ProjectPlan:
    plan_id: str
    objective: str
    planner_agent_profile_id: UUID | None
    generated_at: str
    strategy: str
    work_packages: tuple[WorkflowNode, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_version": 1,
            "plan_id": self.plan_id,
            "objective": self.objective,
            "planner_agent_profile_id": str(self.planner_agent_profile_id)
            if self.planner_agent_profile_id is not None
            else None,
            "generated_at": self.generated_at,
            "strategy": self.strategy,
            "work_packages": [_package_dict(package) for package in self.work_packages],
        }


def _package_dict(package: WorkflowNode) -> dict[str, object]:
    payload = package.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key in (
        "node_type",
        "required_tools",
        "required_mcp_tools",
        "required_resource_ids",
        "resource_requirements",
        "estimated_cost_usd",
        "join_policy",
        "locked",
        "tool_name",
        "arguments",
        "output_schema",
        "input_bindings",
        "subworkflow_definition_id",
        "subworkflow_version",
    ):
        value = payload.get(key)
        if value in (None, False, 0, {}, [], "all_success", "agent"):
            payload.pop(key, None)
    return payload


class MatureOrgProjectPlanBuilder:
    def __init__(self, matcher: MemberMatchingService) -> None:
        self._leadership = LeadershipPackageAppender()
        self._execution = ExecutionPackageAppender(matcher)

    def create_initial_plan(self, task: Task) -> dict[str, object] | None:
        context = PlanningContext.from_task(task)
        if context is None:
            return None

        work_packages: list[WorkflowNode] = []
        executive_alignment_ids = self._leadership.append_executive_alignment(
            work_packages=work_packages,
            executives=context.executives,
        )
        manager_package_id = self._leadership.append_manager_planning(
            work_packages=work_packages,
            manager_planner_id=context.manager_planner_id,
            executive_alignment_ids=executive_alignment_ids,
        )
        lead_package_ids = self._leadership.append_lead_breakdown(
            work_packages=work_packages,
            leads=context.leads,
            manager_package_id=manager_package_id,
            executive_alignment_ids=executive_alignment_ids,
        )
        execution_package_ids, execution_ids_by_lead = self._execution.append(
            work_packages=work_packages,
            task=task,
            context=context,
            lead_package_ids=lead_package_ids,
            manager_package_id=manager_package_id,
            executive_alignment_ids=executive_alignment_ids,
        )
        lead_review_ids = self._leadership.append_lead_review(
            work_packages=work_packages,
            leads=context.leads,
            lead_package_ids=lead_package_ids,
            execution_ids_by_lead=execution_ids_by_lead,
            has_manager=context.manager_planner_id is not None,
            has_executives=bool(context.executives),
        )
        manager_summary_id = self._leadership.append_manager_summary(
            work_packages=work_packages,
            manager_planner_id=context.manager_planner_id,
            dependencies=lead_review_ids
            or execution_package_ids
            or list(lead_package_ids.values()),
            has_executives=bool(context.executives),
        )
        self._leadership.append_executive_approval(
            work_packages=work_packages,
            executives=context.executives,
            manager_summary_id=manager_summary_id,
            fallback_dependencies=lead_review_ids or execution_package_ids,
        )
        return self._validated_plan(task, context, work_packages)

    def _validated_plan(
        self,
        task: Task,
        context: PlanningContext,
        work_packages: list[WorkflowNode],
    ) -> dict[str, object]:
        plan = ProjectPlan(
            plan_id=str(uuid4()),
            objective=task.title,
            planner_agent_profile_id=context.planner_agent_profile_id,
            generated_at=datetime.now(UTC).isoformat(),
            strategy="deterministic_team_snapshot_v1",
            work_packages=tuple(work_packages),
        ).as_dict()
        validate_project_plan(plan, task.team_snapshot)
        return plan


class ProjectPlanningService:
    def __init__(self, matcher: MemberMatchingService | None = None) -> None:
        self._builder = MatureOrgProjectPlanBuilder(matcher or MemberMatchingService())

    def create_initial_plan(self, task: Task) -> dict[str, object] | None:
        return self._builder.create_initial_plan(task)


__all__ = [
    "MatureOrgProjectPlanBuilder",
    "PlanningContext",
    "ProjectPlan",
    "ProjectPlanningService",
    "WorkflowNode",
]
