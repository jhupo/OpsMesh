from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.project_plan_context import PlanningContext
from backend.app.planning.project_plan_execution import ExecutionPackageAppender
from backend.app.planning.project_plan_leadership import LeadershipPackageAppender
from backend.app.planning.project_plan_models import ProjectPlan
from backend.app.planning.project_plan_validation import validate_project_plan
from backend.app.planning.workflow_contracts import WorkflowNode
from backend.app.tasks.models import Task


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
