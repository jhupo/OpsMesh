from uuid import uuid4

from backend.app.planning.project_plans import (
    ProjectPlanValidationError,
    validate_project_plan,
)


def test_project_plan_validation_rejects_agent_outside_team_snapshot() -> None:
    allowed_agent_id = uuid4()
    foreign_agent_id = uuid4()
    snapshot = {
        "team": {"manager_agent_profile_id": str(allowed_agent_id)},
        "members": [],
    }
    plan = {
        "work_packages": [
            {
                "package_id": "bad",
                "title": "Bad package",
                "required_role": "developer",
                "assigned_agent_profile_id": str(foreign_agent_id),
                "depends_on": [],
            }
        ]
    }

    try:
        validate_project_plan(plan, snapshot)
    except ProjectPlanValidationError as exc:
        assert "not in team snapshot" in str(exc)
    else:
        raise AssertionError("Expected foreign assigned agent to be rejected")


def test_project_plan_validation_rejects_unknown_dependency() -> None:
    agent_id = uuid4()
    snapshot = {
        "team": {"manager_agent_profile_id": str(agent_id)},
        "members": [],
    }
    plan = {
        "work_packages": [
            {
                "package_id": "implementation",
                "title": "Implementation",
                "required_role": "developer",
                "assigned_agent_profile_id": str(agent_id),
                "depends_on": ["missing"],
            }
        ]
    }

    try:
        validate_project_plan(plan, snapshot)
    except ProjectPlanValidationError as exc:
        assert "Unknown work package dependency" in str(exc)
    else:
        raise AssertionError("Expected unknown dependency to be rejected")
