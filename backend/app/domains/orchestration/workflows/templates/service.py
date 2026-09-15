from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.workflows.definitions.contracts import WorkflowNode
from backend.app.domains.orchestration.workflows.planning.member_matching import (
    MemberMatchingService,
)
from backend.app.domains.orchestration.workflows.templates.builder import (
    MatureOrgProjectPlanBuilder,
)
from backend.app.domains.orchestration.workflows.templates.models import ProjectPlan
from backend.app.domains.orchestration.workflows.templates.validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)


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
