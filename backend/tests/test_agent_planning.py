from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult, AgentRuntimeStructuredOutput
from backend.app.agents.models import AgentProfile
from backend.app.orchestration.planner_completion import PlannerCompletionService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.plan_lifecycle import TaskPlanLifecycleService, TaskPlanRetryCommand
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.consumer import consume_once
from backend.app.workers.queue.redis_queue import RedisQueue
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


def result_for(agent: AgentProfile, *, invalid: bool = False) -> AgentRunResult:
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
    return AgentRunResult(
        final_output="Plan generated",
        structured_output=AgentRuntimeStructuredOutput(
            value=proposal,
            schema_name="task_plan",
            schema_version="1",
            validated=True,
        ),
    )


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
