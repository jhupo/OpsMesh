"""Reusable organization and project workflow templates.

The public service exports are loaded lazily so leaf template helpers can be
imported by the planning domain without creating a package-level cycle.
"""

__all__ = [
    "ProjectPlan",
    "ProjectPlanValidationError",
    "ProjectPlanningService",
    "validate_project_plan",
]


def __getattr__(name: str):
    if name == "ProjectPlan":
        from backend.app.domains.orchestration.workflows.templates.models import ProjectPlan

        return ProjectPlan
    if name == "ProjectPlanningService":
        from backend.app.domains.orchestration.workflows.templates.service import (
            ProjectPlanningService,
        )

        return ProjectPlanningService
    if name in {"ProjectPlanValidationError", "validate_project_plan"}:
        from backend.app.domains.orchestration.workflows.templates.validation import (
            ProjectPlanValidationError,
            validate_project_plan,
        )

        return {
            "ProjectPlanValidationError": ProjectPlanValidationError,
            "validate_project_plan": validate_project_plan,
        }[name]
    raise AttributeError(name)
