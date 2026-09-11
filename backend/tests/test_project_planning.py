from uuid import uuid4

from backend.app.planning.project_plans import (
    ProjectPlanningService,
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.tasks.models import Task


def test_initial_plan_uses_mature_org_hierarchy() -> None:
    ceo_id = uuid4()
    cto_id = uuid4()
    pm_id = uuid4()
    lead_id = uuid4()
    member_id = uuid4()
    lead_member_id = uuid4()
    snapshot = {
        "team": {"manager_agent_profile_id": str(pm_id)},
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(ceo_id),
                "team_role": "CEO",
                "order_index": 0,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(cto_id),
                "team_role": "CTO",
                "order_index": 1,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(pm_id),
                "team_role": "project_manager",
                "order_index": 2,
            },
            {
                "id": str(lead_member_id),
                "agent_profile_id": str(lead_id),
                "team_role": "team_lead",
                "department": "Engineering",
                "order_index": 3,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(member_id),
                "reports_to_member_id": str(lead_member_id),
                "team_role": "backend_engineer",
                "department": "Engineering",
                "skill_weights": {"python": 1.0},
                "order_index": 4,
            },
        ],
    }
    task = Task(workspace_id=uuid4(), title="Ship control plane", team_snapshot=snapshot)

    plan = ProjectPlanningService().create_initial_plan(task)

    assert plan is not None
    packages = plan["work_packages"]
    by_id = {package["package_id"]: package for package in packages}
    package_ids = [package["package_id"] for package in packages]
    assert package_ids == [
        "executive-alignment-ceo",
        "executive-alignment-cto",
        "manager-planning",
        "lead-breakdown-engineering",
        "backend_engineer-1",
        "lead-review-engineering",
        "manager-summary",
        "executive-approval-ceo",
        "executive-approval-cto",
    ]
    assert by_id["manager-planning"]["assigned_agent_profile_id"] == str(pm_id)
    assert by_id["manager-planning"]["depends_on"] == [
        "executive-alignment-ceo",
        "executive-alignment-cto",
    ]
    assert by_id["lead-breakdown-engineering"]["depends_on"] == ["manager-planning"]
    assert by_id["backend_engineer-1"]["assigned_agent_profile_id"] == str(member_id)
    assert by_id["backend_engineer-1"]["depends_on"] == ["lead-breakdown-engineering"]
    assert by_id["lead-review-engineering"]["depends_on"] == ["backend_engineer-1"]
    assert by_id["manager-summary"]["depends_on"] == ["lead-review-engineering"]
    assert by_id["executive-approval-ceo"]["depends_on"] == ["manager-summary"]
    assert by_id["executive-approval-cto"]["depends_on"] == ["manager-summary"]
    assert not [
        package
        for package in packages
        if package["assigned_agent_profile_id"] in {str(ceo_id), str(cto_id)}
        and package["package_id"]
        not in {
            "executive-alignment-ceo",
            "executive-alignment-cto",
            "executive-approval-ceo",
            "executive-approval-cto",
        }
    ]


def test_requested_work_packages_attach_to_matching_department_member_after_lead_plan() -> None:
    pm_id = uuid4()
    lead_member_id = uuid4()
    lead_id = uuid4()
    frontend_id = uuid4()
    backend_id = uuid4()
    snapshot = {
        "team": {"manager_agent_profile_id": str(pm_id)},
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(pm_id),
                "team_role": "project_manager",
            },
            {
                "id": str(lead_member_id),
                "agent_profile_id": str(lead_id),
                "team_role": "team_lead",
                "department": "Engineering",
                "skill_weights": {"planning": 1.0},
                "order_index": 1,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(frontend_id),
                "reports_to_member_id": str(lead_member_id),
                "team_role": "frontend_engineer",
                "department": "Engineering",
                "skill_weights": {"react": 1.0},
                "order_index": 2,
            },
            {
                "id": str(uuid4()),
                "agent_profile_id": str(backend_id),
                "reports_to_member_id": str(lead_member_id),
                "team_role": "backend_engineer",
                "department": "Engineering",
                "skill_weights": {"python": 1.0},
                "order_index": 3,
            },
        ],
    }
    task = Task(
        workspace_id=uuid4(),
        title="Build dashboard",
        team_snapshot=snapshot,
        input={
            "work_packages": [
                {
                    "package_id": "frontend-build",
                    "title": "Frontend build",
                    "required_role": "frontend_engineer",
                    "required_skills": ["react"],
                    "department": "Engineering",
                }
            ]
        },
    )

    plan = ProjectPlanningService().create_initial_plan(task)

    assert plan is not None
    by_id = {package["package_id"]: package for package in plan["work_packages"]}
    assert by_id["lead-breakdown-engineering"]["depends_on"] == ["manager-planning"]
    assert by_id["frontend-build"]["assigned_agent_profile_id"] == str(frontend_id)
    assert by_id["frontend-build"]["depends_on"] == ["lead-breakdown-engineering"]
    assert by_id["lead-review-engineering"]["depends_on"] == ["frontend-build"]
    assert by_id["manager-summary"]["depends_on"] == ["lead-review-engineering"]


def test_small_team_without_lead_still_generates_usable_plan() -> None:
    developer_id = uuid4()
    snapshot = {
        "team": {},
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(developer_id),
                "team_role": "developer",
                "skill_weights": {"python": 1.0},
            }
        ],
    }
    task = Task(workspace_id=uuid4(), title="Fix bug", team_snapshot=snapshot)

    plan = ProjectPlanningService().create_initial_plan(task)

    assert plan is not None
    assert plan["planner_agent_profile_id"] == str(developer_id)
    assert plan["work_packages"] == [
        {
            "package_id": "developer-1",
            "title": "developer execution",
            "description": "Complete the assigned developer work package for the task.",
            "required_role": "developer",
            "required_skills": ["python"],
            "assigned_agent_profile_id": str(developer_id),
            "depends_on": [],
            "expected_artifacts": ["implementation_artifact"],
            "acceptance_criteria": [
                "The work package produces a clear result summary.",
                "Any generated files or artifacts are attached to the task.",
            ],
            "review_policy": {"reviewer": "manager", "mode": "manager_review"},
        }
    ]


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
