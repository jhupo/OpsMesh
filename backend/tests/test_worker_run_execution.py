import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_task_start_creates_queued_run_and_worker_completes_fake_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    run = orchestration.create_queued_run_for_task(task)
    enqueued = orchestration.enqueue_run(run, requested_by_user_id=user.id)
    session.commit()

    assert enqueued is True
    assert task.status == TaskStatus.QUEUED.value
    assert run.status == RunStatus.QUEUED.value

    handled = consume_once(queue, WorkerJobHandler(session, queue).handle)

    stored_run = session.get(AgentRun, run.id)
    stored_task = session.get(Task, task.id)
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()

    assert handled is True
    assert stored_run is not None
    assert stored_run.status == RunStatus.COMPLETED.value
    assert stored_run.output == {"final_output": "fake_run_completed"}
    assert stored_task is not None
    assert stored_task.status == TaskStatus.COMPLETED.value
    assert [event.event_type for event in events] == ["run.started", "run.completed"]


def test_team_task_runs_manager_specialists_and_summary_in_order() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Plan and review the team work.",
        model="manager-model",
    )
    researcher = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        instructions="Collect market facts.",
        model="researcher-model",
    )
    analyst = AgentProfile(
        workspace_id=workspace.id,
        name="Analyst",
        role="analyst",
        instructions="Analyze the collected facts.",
        model="analyst-model",
    )
    session.add_all([manager, researcher, analyst])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Market Team",
        team_type="research",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=researcher.id,
                team_role="Research",
                order_index=0,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=analyst.id,
                team_role="Analysis",
                order_index=1,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Q2 market analysis",
        description="Produce a concise market analysis.",
    )
    session.add(task)
    session.flush()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    first_run = orchestration.create_queued_run_for_task(task)
    orchestration.enqueue_run(first_run, requested_by_user_id=user.id)
    session.commit()

    handler = WorkerJobHandler(session, queue)
    handled_jobs = 0
    while consume_once(queue, handler.handle):
        handled_jobs += 1

    steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.order_index)
    ).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()
    runs_by_step_id = {run.task_step_id: run for run in runs}
    ordered_runs = [runs_by_step_id[step.id] for step in steps]

    assert handled_jobs == 4
    assert [step.title for step in steps] == [
        "Manager planning",
        "Research execution",
        "Analysis execution",
        "Manager summary",
    ]
    assert steps[1].work_package_id == "Research-1"
    assert steps[1].required_role == "Research"
    assert steps[2].work_package_id == "Analysis-2"
    assert steps[3].work_package_id == "manager-summary"
    assert steps[3].expected_artifacts == ["final_delivery"]
    assert steps[3].review_policy == {"reviewer": "user", "mode": "final_acceptance"}
    assert [step.status for step in steps] == ["completed"] * 4
    assert [step.result_summary for step in steps] == ["fake_run_completed"] * 4
    assert [run.agent_profile_id for run in ordered_runs] == [
        manager.id,
        researcher.id,
        analyst.id,
        manager.id,
    ]
    assert [run.model for run in ordered_runs] == [
        "manager-model",
        "researcher-model",
        "analyst-model",
        "manager-model",
    ]
    assert [run.status for run in ordered_runs] == [RunStatus.COMPLETED.value] * 4
    assert task.status == TaskStatus.COMPLETED.value
    assert task.final_output is not None
    assert task.final_output["final_output"] == "fake_run_completed"
    assert task.final_output["team_orchestration"] == {
        "steps": [
                {
                    "task_step_id": str(step.id),
                    "title": step.title,
                    "status": "completed",
                    "work_package_id": step.work_package_id,
                    "required_role": step.required_role,
                    "agent_profile_id": str(step.assigned_agent_profile_id),
                    "result_summary": "fake_run_completed",
                }
            for step in steps
        ]
    }


def test_team_task_enqueues_dependency_free_specialists_in_parallel() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Plan and review the team work.",
        model="manager-model",
    )
    researcher = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        instructions="Collect market facts.",
        model="researcher-model",
    )
    analyst = AgentProfile(
        workspace_id=workspace.id,
        name="Analyst",
        role="analyst",
        instructions="Analyze the collected facts.",
        model="analyst-model",
    )
    session.add_all([manager, researcher, analyst])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Market Team",
        team_type="research",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=researcher.id,
                team_role="Research",
                order_index=0,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=analyst.id,
                team_role="Analysis",
                order_index=1,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Q2 market analysis",
        description="Produce a concise market analysis.",
    )
    session.add(task)
    session.flush()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    first_run = orchestration.create_queued_run_for_task(task)
    orchestration.enqueue_run(first_run, requested_by_user_id=user.id)
    session.commit()

    handler = WorkerJobHandler(session, queue)
    assert consume_once(queue, handler.handle) is True
    assert queue.count_queued(workspace_id=workspace.id) == 2

    active_specialist_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.task_id == task.id,
            AgentRun.status == RunStatus.QUEUED.value,
        )
    ).all()
    active_specialist_steps = [
        session.get(TaskStep, run.task_step_id) for run in active_specialist_runs
    ]
    assert {step.work_package_id for step in active_specialist_steps if step is not None} == {
        "Research-1",
        "Analysis-2",
    }

    assert consume_once(queue, handler.handle) is True
    session.refresh(task)
    assert task.status == TaskStatus.RUNNING.value
    assert queue.count_queued(workspace_id=workspace.id) == 1
    summary_run_before_ready = session.scalar(
        select(AgentRun)
        .join(TaskStep, AgentRun.task_step_id == TaskStep.id)
        .where(TaskStep.task_id == task.id, TaskStep.work_package_id == "manager-summary")
    )
    assert summary_run_before_ready is None

    assert consume_once(queue, handler.handle) is True
    assert queue.count_queued(workspace_id=workspace.id) == 1
    summary_run = session.scalar(
        select(AgentRun)
        .join(TaskStep, AgentRun.task_step_id == TaskStep.id)
        .where(TaskStep.task_id == task.id, TaskStep.work_package_id == "manager-summary")
    )
    assert summary_run is not None
    assert summary_run.status == RunStatus.QUEUED.value

    assert consume_once(queue, handler.handle) is True
    session.refresh(task)
    assert task.status == TaskStatus.COMPLETED.value


def test_pm_summary_acceptance_completes_task_with_structured_decision() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task, summary_step, manager = _seed_summary_ready_task(session, user.id, workspace.id)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=summary_step.id,
        agent_profile_id=manager.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    output = json.dumps(
        {
            "decision": "approved",
            "summary": "Final package is ready.",
            "reasons": ["All criteria passed."],
        }
    )
    orchestration = RunOrchestrationService(session)
    orchestration._mark_run_started(run)
    orchestration._mark_run_completed(run, output, requested_by_user_id=user.id)
    session.flush()

    session.refresh(task)
    session.refresh(summary_step)

    assert task.status == TaskStatus.COMPLETED.value
    assert task.final_output is not None
    assert task.final_output["final_output"] == "Final package is ready."
    assert task.final_output["pm_acceptance"] == {
        "decision": "approved",
        "summary": "Final package is ready.",
        "reasons": ["All criteria passed."],
        "revision_requests": [],
        "missing_work_packages": [],
        "review_policy": {"reviewer": "user", "mode": "final_acceptance"},
        "raw_output": {
            "decision": "approved",
            "summary": "Final package is ready.",
            "reasons": ["All criteria passed."],
        },
    }
    assert summary_step.result_summary == "Final package is ready."


def test_pm_summary_revision_decision_materializes_follow_up_steps() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task, summary_step, manager = _seed_summary_ready_task(session, user.id, workspace.id)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=summary_step.id,
        agent_profile_id=manager.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    output = json.dumps(
        {
            "decision": "request_revision",
            "summary": "Needs one more research pass.",
            "reasons": ["Market size evidence is thin."],
            "revision_requests": [
                {"work_package_id": "Research-1", "instruction": "Add TAM/SAM/SOM sources."}
            ],
        }
    )
    orchestration = RunOrchestrationService(session)
    orchestration._mark_run_started(run)
    orchestration._mark_run_completed(run, output, requested_by_user_id=user.id)
    session.flush()

    session.refresh(task)

    assert task.status == TaskStatus.RUNNING.value
    assert task.completed_at is None
    assert task.final_output is not None
    assert task.final_output["final_output"] == "Needs one more research pass."
    assert task.final_output["pm_acceptance"]["decision"] == "request_revision"
    assert task.final_output["pm_acceptance"]["revision_requests"] == [
        {"work_package_id": "Research-1", "instruction": "Add TAM/SAM/SOM sources."}
    ]
    follow_up_steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.order_index)
    ).all()
    assert [step.work_package_id for step in follow_up_steps] == [
        "Research-1",
        "manager-summary",
        "revision-Research-1-1-1",
        "manager-summary-revision-1",
    ]
    revision_step = follow_up_steps[2]
    review_step = follow_up_steps[3]
    assert revision_step.status == "queued"
    assert revision_step.description == "Add TAM/SAM/SOM sources."
    assert revision_step.assigned_agent_profile_id is not None
    assert revision_step.dependencies["revision_of_work_package_id"] == "Research-1"
    assert review_step.dependencies["after_step_ids"] == [str(revision_step.id)]
    assert review_step.review_policy == {"reviewer": "user", "mode": "final_acceptance"}


def test_team_task_persists_auditable_task_messages() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task, summary_step, manager = _seed_summary_ready_task(session, user.id, workspace.id)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=summary_step.id,
        agent_profile_id=manager.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    output = json.dumps(
        {
            "decision": "request_revision",
            "summary": "Needs one more research pass.",
            "reasons": ["Market size evidence is thin."],
            "revision_requests": [
                {"work_package_id": "Research-1", "instruction": "Add TAM/SAM/SOM sources."}
            ],
        }
    )
    orchestration = RunOrchestrationService(session)
    orchestration._mark_run_started(run)
    orchestration._mark_run_completed(run, output, requested_by_user_id=user.id)
    session.flush()

    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.sequence)
    ).all()

    assert [message.sequence for message in messages] == [1, 2, 3, 4]
    assert [message.message_type for message in messages] == [
        "step.started",
        "step.completed",
        "pm.acceptance_decision",
        "pm.follow_up_created",
    ]
    assert messages[0].task_step_id == summary_step.id
    assert messages[0].agent_run_id == run.id
    assert messages[0].agent_profile_id == manager.id
    assert messages[1].payload["work_package_id"] == "manager-summary"
    assert messages[2].payload["decision"] == "request_revision"
    assert messages[2].payload["revision_requests"] == [
        {"work_package_id": "Research-1", "instruction": "Add TAM/SAM/SOM sources."}
    ]
    assert messages[3].payload["revision_cycle"] == 1
    assert messages[3].payload["follow_up_work_package_ids"] == [
        "revision-Research-1-1-1"
    ]


def test_create_queued_run_for_task_reuses_active_team_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Plan and review.",
    )
    session.add(manager)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Solo Managed Team",
        team_type="general",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Prepare plan",
    )
    session.add(task)
    session.flush()

    orchestration = RunOrchestrationService(session)
    first_run = orchestration.create_queued_run_for_task(task)
    second_run = orchestration.create_queued_run_for_task(task)

    runs = session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()
    steps = session.scalars(select(TaskStep).where(TaskStep.task_id == task.id)).all()

    assert second_run.id == first_run.id
    assert len(runs) == 1
    assert len(steps) == 1


def test_team_task_orchestration_uses_frozen_team_snapshot() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        model="manager-model",
    )
    original_developer = AgentProfile(
        workspace_id=workspace.id,
        name="Original Developer",
        role="frontend_engineer",
        model="original-model",
    )
    new_developer = AgentProfile(
        workspace_id=workspace.id,
        name="New Developer",
        role="frontend_engineer",
        model="new-model",
    )
    session.add_all([manager, original_developer, new_developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Product Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    original_member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=original_developer.id,
        team_role="frontend_engineer",
        department="Engineering",
        skill_weights={"react": 0.9},
        order_index=0,
    )
    session.add(original_member)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        team_snapshot={
            "snapshot_version": 1,
            "team": {
                "id": str(team.id),
                "name": team.name,
                "manager_agent_profile_id": str(manager.id),
            },
            "members": [
                {
                    "id": str(original_member.id),
                    "agent_profile_id": str(original_developer.id),
                    "team_role": "frontend_engineer",
                    "order_index": 0,
                    "accepts_tasks": True,
                }
            ],
            "agents": [],
        },
        title="Build dashboard",
    )
    original_member.status = "inactive"
    replacement_member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=new_developer.id,
        team_role="frontend_engineer",
        order_index=0,
    )
    session.add_all([task, replacement_member])
    session.flush()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    first_run = orchestration.create_queued_run_for_task(task)
    orchestration.enqueue_run(first_run, requested_by_user_id=user.id)
    session.commit()

    handler = WorkerJobHandler(session, queue)
    while consume_once(queue, handler.handle):
        pass

    steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.order_index)
    ).all()
    runs = session.scalars(select(AgentRun).where(AgentRun.task_id == task.id)).all()

    assert [step.assigned_agent_profile_id for step in steps] == [
        manager.id,
        original_developer.id,
        manager.id,
    ]
    assert [step.work_package_id for step in steps] == [
        "manager-planning",
        "frontend_engineer-1",
        "manager-summary",
    ]
    assert steps[1].required_role == "frontend_engineer"
    assert new_developer.id not in {run.agent_profile_id for run in runs}
    assert task.status == TaskStatus.COMPLETED.value


def test_worker_rejects_workspace_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()

    bad_job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="bad",
    )

    try:
        WorkerJobHandler(session).handle(bad_job)
    except ValueError as exc:
        assert "workspace mismatch" in str(exc)
    else:
        raise AssertionError("Expected workspace mismatch to raise")


def test_failed_worker_job_is_retried_by_queue() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="retry-demo",
        max_attempts=2,
    )
    queue.enqueue(job)

    try:
        consume_once(queue, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected failed handler to raise")

    retried = queue.dequeue()
    assert retried is not None
    assert retried.attempt == 1


def test_worker_persists_failed_run_event() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()

    class FailingRunner:
        async def run(self, request):
            raise RuntimeError("model failed")

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="fail-run",
    )

    try:
        WorkerJobHandler(session, agent_runner=FailingRunner()).handle(job)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected failing runner to raise")

    stored_run = session.get(AgentRun, run.id)
    failed_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "run.failed",
        )
    )

    assert stored_run is not None
    assert stored_run.status == RunStatus.FAILED.value
    assert stored_run.error == {
        "code": "RuntimeError",
        "message": "model failed",
        "retryable": True,
    }
    assert failed_event is not None


def test_agent_request_includes_profile_tool_policy_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        tool_policy={"mcp_tools": ["generate_image", 42, "write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="tool-policy-context",
    )
    request = RunOrchestrationService(session)._build_agent_request(run, job)

    assert request.context.allowed_tools == ("generate_image", "write_artifact")
    assert request.context.metadata == {
        "agent_profile_id": str(agent.id),
        "agent_role": "designer",
        "run_model": agent.model,
        "model_provider_credential_id": None,
    }


def test_agent_request_includes_authorized_task_step_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Build pitch deck")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        tool_policy={"allowed_tools": ["generate_image", "write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        work_package_id="visual-design",
        required_role="designer",
        required_skills=["brand_design", "deck_layout"],
        expected_artifacts=["pitch_deck"],
        acceptance_criteria=["Deck follows the brand system."],
        review_policy={"reviewer": "manager", "mode": "manager_review"},
        title="Visual design",
        description="Create the visual system for the pitch deck.",
        status="queued",
        order_index=100,
        dependencies={},
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="step-context",
    )
    request = RunOrchestrationService(session)._build_agent_request(run, job)

    assert request.context.allowed_tools == ("generate_image", "write_artifact")
    assert request.context.metadata == {
        "agent_profile_id": str(agent.id),
        "agent_role": "designer",
        "run_model": agent.model,
        "model_provider_credential_id": None,
        "context_scope": "task_step",
        "task_step_id": str(step.id),
        "work_package_id": "visual-design",
        "required_role": "designer",
        "required_skills": ["brand_design", "deck_layout"],
        "expected_artifacts": ["pitch_deck"],
        "acceptance_criteria": ["Deck follows the brand system."],
        "review_policy": {"reviewer": "manager", "mode": "manager_review"},
    }


def test_agent_request_resolves_agent_model_provider_override() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Custom Provider",
        provider="openai-compatible",
        api_key="sk-custom",
        default_model="provider-default-model",
        base_url="https://llm.example.test/v1",
        is_default=True,
    )
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Custom",
        role="writer",
        instructions="Write.",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    request = RunOrchestrationService(session, settings=settings)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="provider-context",
        ),
    )

    assert request.model == "provider-default-model"
    assert request.base_url == "https://llm.example.test/v1"
    assert request.api_key == "sk-custom"
    assert request.model_provider_credential_id == credential.id
    assert request.context.metadata["model_provider_credential_id"] == str(credential.id)


def test_stale_running_runs_are_recovered_as_failed() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        input={},
        started_at=datetime.now(UTC) - timedelta(seconds=3_600),
    )
    session.add(run)
    session.commit()

    summary = RunOrchestrationService(session).recover_stale_running_runs(
        stale_after_seconds=900,
    )

    event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "run.recovered_failed",
        )
    )
    assert summary.recovered_runs == 1
    assert run.status == RunStatus.FAILED.value
    assert run.error == {
        "code": "stale_worker_run",
        "message": "Worker stopped reporting before the run completed",
        "retryable": True,
    }
    assert task.status == TaskStatus.FAILED.value
    assert event is not None


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _seed_summary_ready_task(
    session: Session,
    user_id: UUID,
    workspace_id: UUID,
) -> tuple[Task, TaskStep, AgentProfile]:
    manager = AgentProfile(
        workspace_id=workspace_id,
        name="Manager",
        role="manager",
        instructions="Review and summarize.",
        model="manager-model",
    )
    researcher = AgentProfile(
        workspace_id=workspace_id,
        name="Researcher",
        role="researcher",
        instructions="Research.",
        model="researcher-model",
    )
    task = Task(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title="Q2 market analysis",
        description="Produce a concise market analysis.",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([manager, researcher, task])
    session.flush()
    research_step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
        assigned_agent_profile_id=researcher.id,
        work_package_id="Research-1",
        required_role="Research",
        required_skills=[],
        expected_artifacts=["work_summary"],
        acceptance_criteria=["Research is complete."],
        review_policy={"reviewer": "manager", "mode": "manager_review"},
        title="Research execution",
        description="Collect market facts.",
        status="completed",
        order_index=100,
        dependencies={},
        result_summary="Research completed.",
    )
    summary_step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
        assigned_agent_profile_id=manager.id,
        work_package_id="manager-summary",
        required_role="project_manager",
        required_skills=["review", "synthesis"],
        expected_artifacts=["final_delivery"],
        acceptance_criteria=["The final answer integrates all completed work packages."],
        review_policy={"reviewer": "user", "mode": "final_acceptance"},
        title="Manager summary",
        description="Review specialist outputs and produce final answer.",
        status="queued",
        order_index=1_000,
        dependencies={"after_step_ids": [str(research_step.id)]},
    )
    session.add_all([research_step, summary_step])
    session.flush()
    return task, summary_step, manager


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
