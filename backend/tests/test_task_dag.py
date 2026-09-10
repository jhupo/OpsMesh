from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.orchestration.run_eligibility import RunEligibilityService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.orchestration.step_dependencies import dependencies_satisfied
from backend.app.orchestration.team_step_project_plan import ProjectPlanStepMaterializer
from backend.app.planning.project_plan_validation import (
    ProjectPlanValidationError,
    validate_project_plan,
)
from backend.app.tasks.models import Task, TaskStep
from backend.tests.test_worker_run_execution import _seed_workspace, _session


@pytest.fixture
def session() -> Iterator[Session]:
    with _session() as value:
        engine = value.get_bind()
        yield value
    engine.dispose()


def package(name: str, dependencies: list[object]) -> dict[str, object]:
    return {
        "package_id": name,
        "title": name,
        "required_role": "worker",
        "assigned_agent_profile_id": None,
        "depends_on": dependencies,
    }


@pytest.mark.parametrize(
    ("packages", "code"),
    [
        ([package("a", ["a"])], "plan_dependency_cycle"),
        ([package("a", ["b"]), package("b", ["a"])], "plan_dependency_cycle"),
        ([package("a", ["missing"])], "plan_missing_dependency"),
        ([package("a", ["b", "b"]), package("b", [])], "plan_duplicate_edge"),
        ([package("a", [{}])], "plan_invalid"),
    ],
)
def test_invalid_dags_fail_before_materialization(packages: list[dict], code: str) -> None:
    with pytest.raises(ProjectPlanValidationError) as error:
        validate_project_plan({"work_packages": packages}, {"team": {}, "members": []})
    assert error.value.code == code


def test_forward_dependency_is_materialized_and_blocks_scheduling(session: Session) -> None:
    _, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        title="DAG",
        status="queued",
        team_snapshot={"team": {}, "members": []},
    )
    session.add(task)
    session.flush()
    first = ProjectPlanStepMaterializer(session).materialize(
        task, {"work_packages": [package("after", ["before"]), package("before", [])]}
    )
    steps = {step.work_package_id: step for step in session.scalars(select(TaskStep))}
    assert first is steps["before"]
    assert steps["after"].dependencies["after_step_ids"] == [str(first.id)]
    eligibility = RunEligibilityService(session)
    assert eligibility.next_eligible_steps(task.id, workspace.id) == [first]
    first.status = "completed"
    session.flush()
    assert eligibility.next_eligible_steps(task.id, workspace.id) == [steps["after"]]


@pytest.mark.parametrize("kind", ["missing", "foreign_workspace", "foreign_task", "malformed"])
def test_missing_and_foreign_dependencies_never_authorize_work(session: Session, kind: str) -> None:
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="own")
    other_task = Task(workspace_id=workspace.id, title="other")
    session.add_all([task, other_task])
    session.flush()
    upstream = TaskStep(
        workspace_id=uuid4() if kind == "foreign_workspace" else workspace.id,
        task_id=other_task.id if kind == "foreign_task" else task.id,
        title="upstream",
        status="completed",
    )
    session.add(upstream)
    session.flush()
    raw = str(uuid4()) if kind == "missing" else str(upstream.id)
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="downstream",
        dependencies={"after_step_ids": "invalid" if kind == "malformed" else [raw]},
    )
    assert not dependencies_satisfied(session, step)
    assert not RunEligibilityService(session).dependencies_satisfied(step)


def test_launch_rechecks_dependencies_after_candidate_selection(session: Session) -> None:
    _, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        title="DAG",
        status="running",
        team_snapshot={"team": {}, "members": []},
    )
    session.add(task)
    session.flush()
    ProjectPlanStepMaterializer(session).materialize(
        task, {"work_packages": [package("before", []), package("after", ["before"])]}
    )
    steps = {step.work_package_id: step for step in session.scalars(select(TaskStep))}
    steps["before"].status = "completed"
    session.flush()
    assert RunEligibilityService(session).next_eligible_steps(task.id, workspace.id) == [
        steps["after"]
    ]
    steps["before"].status = "failed"
    session.flush()
    launcher = RunOrchestrationService(session)._run_step_launcher()
    assert launcher.lock_step_for_scheduling(task, steps["after"]) is None
