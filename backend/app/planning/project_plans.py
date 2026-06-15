from backend.app.planning.member_matching import MemberMatchingService
from backend.app.planning.project_plan_builder import MatureOrgProjectPlanBuilder
from backend.app.planning.project_plan_models import ProjectPlan, ProjectWorkPackage
from backend.app.planning.project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)
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
    "ProjectWorkPackage",
    "validate_project_plan",
]
