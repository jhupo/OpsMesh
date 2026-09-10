from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult, AgentRuntimeStructuredOutput
from backend.app.agents.models import AgentProfile
from backend.app.costs.models import WorkspaceCostBudget
from backend.app.orchestration.planner_completion import PlannerCompletionService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.future_plan_mutation import (
    TaskPlanMutationCommand,
    TaskPlanMutationError,
    TaskPlanMutationService,
)
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.plan_lifecycle import TaskPlanLifecycleService, TaskPlanRetryCommand
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.consumer import consume_once
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import WorkspaceQuota
from backend.tests.test_worker_run_execution import (
    _build_agent_request,
    _seed_workspace,
    _session,
    approve_resource_reviews_by_default,  # noqa: F401
)


@pytest.fixture
def planning() -> Iterator[tuple[Session, Task, AgentProfile, AgentRun]]:
    with _session() as session:
        engine = session.get_bind()
        user, workspace = _seed_workspace(session)
        agent = AgentProfile(workspace_id=workspace.id, name="Planner", role="manager")
        session.add(agent)
        session.flush()
        team = AgentTeam(
            workspace_id=workspace.id,
            name="Team",
            manager_agent_profile_id=agent.id,
        )
        session.add(team)
        session.flush()
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=agent.id,
                team_role="manager",
            )
        )
        task = Task(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            created_by_user_id=user.id,
            title="Produce a report",
            input={},
        )
        session.add(task)
        session.flush()
        run = RunOrchestrationService(session).create_queued_run_for_task(task)
        assert run is not None
        session.commit()
        yield session, task, agent, run
    engine.dispose()


def result_for(
    agent: AgentProfile,
    *,
    invalid: bool = False,
    package_overrides: dict[str, object] | None = None,
) -> AgentRunResult:
    proposal = {
        "objective": "Produce report",
        "work_packages": [
            {
                "package_id": "report",
                "title": "Write report",
                "description": "Compare facts",
                "required_role": "manager",
                "required_skills": [],
                "assigned_agent_profile_id": str(uuid4() if invalid else agent.id),
                "depends_on": [],
                "expected_artifacts": [],
                "acceptance_criteria": ["Contains a sourced conclusion"],
            }
        ],
    }
    if package_overrides:
        proposal["work_packages"][0].update(package_overrides)
    return AgentRunResult(
        final_output="Plan generated",
        structured_output=AgentRuntimeStructuredOutput(
            value=proposal,
            schema_name="task_plan",
            schema_version="2",
            validated=True,
        ),
    )


def _mutation_package(agent: AgentProfile, package_id: str) -> dict[str, object]:
    return {
        "package_id": package_id,
        "title": package_id.replace("-", " ").title(),
        "description": "Complete the future follow-up work.",
        "required_role": "manager",
        "required_skills": [],
        "assigned_agent_profile_id": str(agent.id),
        "depends_on": [],
        "expected_artifacts": [],
        "acceptance_criteria": ["The follow-up is complete."],
    }


def _plan_package(task: Task, package_id: str) -> dict[str, object]:
    assert isinstance(task.project_plan, dict)
    packages = task.project_plan["work_packages"]
    assert isinstance(packages, list)
    package = next(item for item in packages if item["package_id"] == package_id)
    assert isinstance(package, dict)
    return package


@pytest.mark.parametrize(
    ("package_overrides", "code"),
    [
        ({"required_role": "designer"}, "plan_agent_role_mismatch"),
        ({"required_skills": ["research"]}, "plan_agent_skill_unavailable"),
    ],
)
def test_plan_rejects_live_assignment_mismatch(planning, package_overrides, code) -> None:
    session, task, agent, run = planning
    assert not PlannerCompletionService(session).apply(
        run,
        result_for(agent, package_overrides=package_overrides),
    )
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.status == "failed"
    assert attempt.validation_errors == [code]
    assert session.scalar(select(func.count(TaskStep.id))) == 1


@pytest.mark.parametrize("stale_target", ["agent", "team"])
def test_plan_rejects_stale_live_authorization(planning, stale_target) -> None:
    session, task, agent, run = planning
    if stale_target == "agent":
        agent.version += 1
        expected_code = "plan_agent_snapshot_stale"
    else:
        team = session.get(AgentTeam, task.agent_team_id)
        assert team is not None
        team.capability_policy_version += 1
        expected_code = "plan_authorization_stale"
    session.flush()
    assert not PlannerCompletionService(session).apply(run, result_for(agent))
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.validation_errors == [expected_code]


@pytest.mark.parametrize(
    ("package_overrides", "code"),
    [
        ({"required_tools": ["not-authorized"]}, "plan_tool_unavailable"),
        ({"required_resource_ids": [str(uuid4())]}, "plan_resource_unavailable"),
    ],
)
def test_plan_rejects_unavailable_capabilities(planning, package_overrides, code) -> None:
    session, task, agent, run = planning
    assert not PlannerCompletionService(session).apply(
        run,
        result_for(agent, package_overrides=package_overrides),
    )
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.validation_errors == [code]
    assert session.scalar(select(func.count(TaskStep.id))) == 1


def test_plan_rejects_runtime_quota_overage(planning) -> None:
    session, task, agent, run = planning
    runtime_space = RuntimeSpace(
        workspace_id=task.workspace_id,
        name="limited-runtime",
        status="active",
    )
    session.add(runtime_space)
    session.flush()
    task.runtime_space_id = runtime_space.id
    session.add(
        RuntimeSpaceQuota(
            workspace_id=task.workspace_id,
            runtime_space_id=runtime_space.id,
            quota_key="memory_mb",
            limit_value=1,
            reserved_value=0,
            unit="mb",
            status="active",
        )
    )
    session.flush()
    assert not PlannerCompletionService(session).apply(
        run,
        result_for(
            agent,
            package_overrides={"resource_requirements": {"memory_mb": 2}},
        ),
    )
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.validation_errors == ["plan_runtime_quota_exceeded"]


def test_plan_rejects_workspace_quota_overage(planning) -> None:
    session, task, agent, run = planning
    runtime_space = RuntimeSpace(
        workspace_id=task.workspace_id,
        name="workspace-quota-runtime",
        status="active",
    )
    session.add(runtime_space)
    session.flush()
    task.runtime_space_id = runtime_space.id
    session.add(
        WorkspaceQuota(
            workspace_id=task.workspace_id,
            quota_key="memory_mb",
            limit_value=1,
            reserved_value=0,
            unit="mb",
            status="active",
        )
    )
    session.flush()
    assert not PlannerCompletionService(session).apply(
        run,
        result_for(
            agent,
            package_overrides={"resource_requirements": {"memory_mb": 2}},
        ),
    )
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.validation_errors == ["plan_workspace_quota_exceeded"]


def test_plan_rejects_projected_cost_budget(planning) -> None:
    session, task, agent, run = planning
    session.add(
        WorkspaceCostBudget(
            workspace_id=task.workspace_id,
            currency="USD",
            monthly_limit=Decimal("1"),
            warning_ratio=Decimal("0.8"),
            enforcement="block",
            enabled=True,
        )
    )
    session.flush()
    assert not PlannerCompletionService(session).apply(
        run,
        result_for(agent, package_overrides={"estimated_cost_usd": 2.0}),
    )
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.validation_errors == ["plan_cost_budget_exceeded"]


def test_plan_preserves_feasibility_requirements_on_steps(planning) -> None:
    session, task, agent, run = planning
    runtime_space = RuntimeSpace(
        workspace_id=task.workspace_id,
        name="roomy-runtime",
        status="active",
    )
    session.add(runtime_space)
    session.flush()
    task.runtime_space_id = runtime_space.id
    session.add(
        RuntimeSpaceQuota(
            workspace_id=task.workspace_id,
            runtime_space_id=runtime_space.id,
            quota_key="memory_mb",
            limit_value=4,
            reserved_value=0,
            unit="mb",
            status="active",
        )
    )
    session.flush()
    assert PlannerCompletionService(session).apply(
        run,
        result_for(
            agent,
            package_overrides={
                "resource_requirements": {"memory_mb": 2},
                "estimated_cost_usd": 0.5,
            },
        ),
    )
    session.commit()
    step = session.scalar(select(TaskStep).where(TaskStep.work_package_id == "report"))
    assert step.dependencies["resource_requirements"] == {"memory_mb": 2}
    assert step.dependencies["estimated_cost_usd"] == 0.5


def test_default_planning_is_a_tool_free_worker_run(planning) -> None:
    session, task, agent, run = planning
    snapshot = run.input["authorization_snapshot"]
    assert snapshot["allowed_tools"] == []
    assert snapshot["agent_tools"] == []
    assert snapshot["output_schema"]["name"] == "task_plan"
    assert snapshot["capability_catalog"]["tools"] == []
    assert session.scalar(select(func.count(TaskStep.id))) == 1
    assert task.project_plan["strategy"] == "agent_planning_pending"
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.status == "queued"
    assert attempt.completed_at is None
    assert run.agent_profile_id == agent.id


def test_structured_plan_creates_work_and_platform_acceptance_once(planning) -> None:
    session, task, agent, run = planning
    completion = PlannerCompletionService(session)
    assert completion.apply(run, result_for(agent))
    session.commit()
    assert completion.apply(run, result_for(agent))
    assert session.scalar(select(func.count(TaskStep.id))) == 3
    steps = {step.work_package_id: step for step in session.scalars(select(TaskStep))}
    assert steps["report"].assigned_agent_profile_id == agent.id
    assert steps["manager-summary"].dependencies["after_step_ids"] == [str(steps["report"].id)]
    assert task.project_plan["strategy"] == "agent_sdk"
    assert session.scalar(select(TaskPlanningAttempt)).status == "completed"


def test_rejected_plan_never_creates_downstream_work(planning) -> None:
    session, task, agent, run = planning
    lifecycle = RunOrchestrationService(session)._run_lifecycle()
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(run, result_for(agent, invalid=True), task.created_by_user_id)
    session.commit()
    assert run.status == "failed"
    assert task.status == "blocked"
    assert session.scalar(select(func.count(TaskStep.id))) == 1
    assert session.scalar(select(TaskPlanningAttempt)).status == "failed"


def test_plain_text_output_cannot_be_promoted_to_a_plan(planning) -> None:
    session, task, _, run = planning
    lifecycle = RunOrchestrationService(session)._run_lifecycle()
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(run, AgentRunResult(final_output="done"), task.created_by_user_id)
    assert task.status == "blocked"
    assert session.scalar(select(func.count(TaskStep.id))) == 1


@pytest.mark.parametrize("failure", ["sdk", "worker_lost", "cancel"])
def test_planner_terminal_outcomes_close_attempt(planning, failure) -> None:
    session, task, _, run = planning
    lifecycle = RunOrchestrationService(session)._run_lifecycle()
    lifecycle.mark_run_started(run)
    if failure == "sdk":
        lifecycle.mark_run_failed(run, RuntimeError("provider failed"))
    elif failure == "worker_lost":
        lifecycle.mark_run_recovered_failed(run)
    else:
        lifecycle.mark_run_cancelled(run, completed_at=datetime.now(UTC))
    session.commit()
    attempt = session.scalar(select(TaskPlanningAttempt))
    assert attempt.status == ("cancelled" if failure == "cancel" else "failed")
    assert attempt.completed_at is not None
    if failure != "cancel":
        assert task.status == "blocked"


def test_retry_creates_new_planner_without_erasing_failed_attempt(planning) -> None:
    session, task, _, run = planning
    lifecycle = RunOrchestrationService(session)._run_lifecycle()
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_failed(run, RuntimeError("provider failed"))
    session.commit()
    TaskPlanLifecycleService(session).retry_task_plan(
        task.workspace_id, task.id, task.created_by_user_id, TaskPlanRetryCommand()
    )
    attempts = session.scalars(
        select(TaskPlanningAttempt).order_by(TaskPlanningAttempt.attempt_number)
    ).all()
    assert [attempt.status for attempt in attempts] == ["failed", "queued"]
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert task.project_plan["plan_id"] == str(attempts[1].id)
    assert session.scalar(select(func.count(TaskStep.id))) == 2
    assert session.scalar(select(func.count(AgentRun.id))) == 2
    assert run.status == "failed"


def test_retry_cannot_replace_live_planning(planning) -> None:
    session, task, _, _ = planning
    with pytest.raises(ValueError, match="failed or blocked"):
        TaskPlanLifecycleService(session).retry_task_plan(
            task.workspace_id, task.id, task.created_by_user_id, TaskPlanRetryCommand()
        )
    assert session.scalar(select(func.count(TaskPlanningAttempt.id))) == 1


def test_planner_request_uses_frozen_sdk_schema_and_roster(planning) -> None:
    session, task, agent, run = planning
    request = _build_agent_request(
        session,
        run,
        JobPayload(
            workspace_id=task.workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            idempotency_key="planner-contract",
        ),
    )
    assert request.output_schema is not None
    assert request.output_schema.name == "task_plan"
    assert str(agent.id) in request.input_text
    assert request.context.allowed_tools == ()


def test_future_plan_mutation_reconciles_steps_and_preserves_history(planning) -> None:
    session, task, agent, run = planning
    assert PlannerCompletionService(session).apply(run, result_for(agent))
    session.commit()

    service = TaskPlanMutationService(session)
    service.apply(
        task.workspace_id,
        task.id,
        task.created_by_user_id,
        TaskPlanMutationCommand(
            operations=(
                {
                    "operation": "add",
                    "package": _mutation_package(agent, "research"),
                },
            ),
            reason="Add research follow-up",
            mutation_id="mutation-add-research",
        ),
    )
    session.refresh(task)
    added = session.scalar(select(TaskStep).where(TaskStep.work_package_id == "research"))
    assert added is not None
    assert added.status == "queued"
    assert "research" in _plan_package(task, "manager-summary")["depends_on"]

    service.apply(
        task.workspace_id,
        task.id,
        task.created_by_user_id,
        TaskPlanMutationCommand(
            operations=(
                {
                    "operation": "reassign",
                    "package_id": "research",
                    "assigned_agent_profile_id": str(agent.id),
                },
            ),
            reason="Keep research with the manager",
            mutation_id="mutation-reassign-research",
        ),
    )
    service.apply(
        task.workspace_id,
        task.id,
        task.created_by_user_id,
        TaskPlanMutationCommand(
            operations=(
                {
                    "operation": "split",
                    "package_id": "research",
                    "packages": [
                        _mutation_package(agent, "research-sources"),
                        _mutation_package(agent, "research-analysis"),
                    ],
                },
            ),
            reason="Split research into evidence and analysis",
            mutation_id="mutation-split-research",
        ),
    )
    session.refresh(added)
    assert added.status == "cancelled"
    assert session.scalar(select(TaskStep).where(TaskStep.work_package_id == "research-sources"))
    assert session.scalar(select(TaskStep).where(TaskStep.work_package_id == "research-analysis"))

    service.apply(
        task.workspace_id,
        task.id,
        task.created_by_user_id,
        TaskPlanMutationCommand(
            operations=(
                {
                    "operation": "merge",
                    "package_ids": ["research-sources", "research-analysis"],
                    "package": _mutation_package(agent, "research-findings"),
                },
            ),
            reason="Merge research outputs",
            mutation_id="mutation-merge-research",
        ),
    )
    merged = session.scalar(select(TaskStep).where(TaskStep.work_package_id == "research-findings"))
    assert merged is not None
    assert merged.status == "queued"
    assert _plan_package(task, "research-sources")["mutation"]["state"] == "cancelled"

    service.apply(
        task.workspace_id,
        task.id,
        task.created_by_user_id,
        TaskPlanMutationCommand(
            operations=(
                {"operation": "cancel", "package_id": "research-findings"},
            ),
            reason="Remove obsolete research",
            mutation_id="mutation-cancel-research",
        ),
    )
    session.refresh(merged)
    assert merged.status == "cancelled"
    assert _plan_package(task, "manager-summary")["depends_on"] == ["report"]
    assert task.project_plan["plan_revision"] == 5
    assert len(task.project_plan["mutation_history"]) == 5


def test_future_plan_mutation_rejects_active_and_completed_side_effects(planning) -> None:
    session, task, agent, run = planning
    assert PlannerCompletionService(session).apply(run, result_for(agent))
    session.commit()
    report = session.scalar(select(TaskStep).where(TaskStep.work_package_id == "report"))
    assert report is not None
    active_run = AgentRun(
        workspace_id=task.workspace_id,
        task_id=task.id,
        task_step_id=report.id,
        agent_profile_id=agent.id,
        status="running",
        input={},
    )
    session.add(active_run)
    session.flush()
    with pytest.raises(TaskPlanMutationError) as active_error:
        TaskPlanMutationService(session).apply(
            task.workspace_id,
            task.id,
            task.created_by_user_id,
            TaskPlanMutationCommand(
                operations=(
                    {"operation": "cancel", "package_id": "report"},
                ),
                reason="Cancel active report",
            ),
        )
    assert active_error.value.code == "plan_mutation_active_side_effect"
    session.rollback()

    session.refresh(report)
    report.status = "completed"
    session.commit()
    with pytest.raises(TaskPlanMutationError) as completed_error:
        TaskPlanMutationService(session).apply(
            task.workspace_id,
            task.id,
            task.created_by_user_id,
            TaskPlanMutationCommand(
                operations=(
                    {"operation": "cancel", "package_id": "report"},
                ),
                reason="Cancel completed report",
            ),
        )
    assert completed_error.value.code == "plan_mutation_completed_immutable"


@pytest.mark.usefixtures("approve_resource_reviews_by_default")
def test_worker_consumes_plan_then_enqueues_work_once(planning) -> None:
    session, task, agent, run = planning

    class PlannerRunner:
        calls = 0

        async def run(self, request):
            self.calls += 1
            assert request.output_schema.name == "task_plan"
            assert request.context.allowed_tools == ()
            return result_for(agent)

    runner = PlannerRunner()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
    )
    RunOrchestrationService(session, queue).enqueue_run(run, task.created_by_user_id)
    session.commit()
    handler = WorkerJobHandler(session, queue, agent_runner=runner)
    assert consume_once(queue, handler.handle)
    assert run.status == "completed"
    assert task.project_plan["strategy"] == "agent_sdk"
    assert queue.count_queued(workspace_id=task.workspace_id) == 1
    handler.handle(
        JobPayload(
            workspace_id=task.workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            idempotency_key="duplicate-planner-delivery",
        )
    )
    assert runner.calls == 1
    assert session.scalar(select(func.count(TaskStep.id))) == 3
