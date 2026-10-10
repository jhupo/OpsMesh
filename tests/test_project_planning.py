from uuid import uuid4

import pytest

from opsmesh.orchestration.definitions.graph import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from opsmesh.orchestration.definitions.templates.builder import ProjectPlanningService
from opsmesh.orchestration.planning.org_structure import build_org_structure
from opsmesh.orchestration.tasks.models import Task


def test_explicit_plan_preserves_custom_role_and_does_not_add_presets() -> None:
    agent_id = uuid4()
    snapshot = {
        "team": {"manager_agent_profile_id": str(agent_id)},
        "members": [{"agent_profile_id": str(agent_id), "team_role": "custom-evaluator"}],
    }
    task = Task(
        workspace_id=uuid4(),
        title="Evaluate",
        team_snapshot=snapshot,
        input={
            "planning_mode": "explicit",
            "work_packages": [
                {
                    "package_id": "evaluate",
                    "title": "Evaluate",
                    "required_role": "custom-evaluator",
                    "assigned_agent_profile_id": str(agent_id),
                }
            ],
        },
    )
    plan = ProjectPlanningService().create_initial_plan(task)
    assert [item["package_id"] for item in plan["work_packages"]] == ["evaluate"]
    assert plan["work_packages"][0]["required_role"] == "custom-evaluator"
    assert plan["strategy"] == "explicit_workflow"


def test_role_names_do_not_assign_management_authority() -> None:
    agent_id = uuid4()
    org = build_org_structure(
        {
            "team": {},
            "members": [
                {"id": str(uuid4()), "agent_profile_id": str(agent_id), "team_role": "CEO"}
            ],
        }
    )
    assert org.managers == ()
    assert org.executives == ()
    assert len(org.contributors) == 1


def test_explicit_planning_requires_user_work() -> None:
    with pytest.raises(ProjectPlanValidationError, match="requires work_packages"):
        ProjectPlanningService().create_initial_plan(
            Task(workspace_id=uuid4(), title="Empty", input={"planning_mode": "explicit"})
        )


def test_project_plan_validation_rejects_agent_outside_team_snapshot() -> None:
    with pytest.raises(ProjectPlanValidationError, match="not in team snapshot"):
        validate_project_plan(
            {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "package_id": "bad",
                        "title": "Bad package",
                        "assigned_agent_profile_id": str(uuid4()),
                        "depends_on": [],
                    }
                ],
            },
            {"team": {"manager_agent_profile_id": str(uuid4())}, "members": []},
        )


def test_project_plan_validation_rejects_unknown_dependency() -> None:
    agent_id = uuid4()
    with pytest.raises(ProjectPlanValidationError, match="Unknown work package dependency"):
        validate_project_plan(
            {
                "planning_mode": "explicit",
                "work_packages": [
                    {
                        "package_id": "implementation",
                        "title": "Implementation",
                        "assigned_agent_profile_id": str(agent_id),
                        "depends_on": ["missing"],
                    }
                ],
            },
            {"team": {"manager_agent_profile_id": str(agent_id)}, "members": []},
        )
