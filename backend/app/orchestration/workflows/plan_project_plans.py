from backend.app.orchestration.workflows.plan_member_matching import MemberMatchingService
from backend.app.orchestration.workflows.plan_project_plan_builder import (
    MatureOrgProjectPlanBuilder,
)
from backend.app.orchestration.workflows.plan_project_plan_models import ProjectPlan
from backend.app.orchestration.workflows.plan_project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.orchestration.workflows.plan_workflow_contracts import WorkflowNode
from backend.app.tasks.models import Task


class ProjectPlanningService:
    def __init__(self, matcher: MemberMatchingService | None = None) -> None:
        self._builder = MatureOrgProjectPlanBuilder(matcher or MemberMatchingService())

    def create_initial_plan(self, task: Task) -> dict[str, object] | None:
        return self._builder.create_initial_plan(task)


__all__ = [
    "ProjectPlanningService",
    "ProjectPlanValidationError",
    "ProjectPlan",
    "WorkflowNode",
    "validate_project_plan",
]
