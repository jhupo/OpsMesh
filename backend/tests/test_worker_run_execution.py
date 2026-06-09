import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult, AgentRuntimeEvent
from backend.app.agent_runtime.sessions import PersistentAgentSession, PersistentAgentSessionItem
from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    Skill,
    WorkspaceSkillInstall,
)
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.activity import activity_phase
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)


class DeterministicAgentRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(final_output="deterministic_run_completed")


class ExplodingAgentRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise AssertionError("runner should not be called")


def test_task_start_creates_queued_run_and_worker_completes_injected_runner() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
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

    handled = consume_once(
        queue,
        WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner()).handle,
    )

    stored_run = session.get(AgentRun, run.id)
    stored_task = session.get(Task, task.id)
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()

    assert handled is True
    assert stored_run is not None
    assert stored_run.status == RunStatus.COMPLETED.value
    assert stored_run.output == {"final_output": "deterministic_run_completed"}
    assert stored_task is not None
    assert stored_task.status == TaskStatus.COMPLETED.value
    assert [event.event_type for event in events] == [
        "run.claimed",
        "run.started",
        "run.context_built",
        "model.request_started",
        "model.response_received",
        "model_provider.used",
        "run.completed",
    ]
    assert events[0].event_metadata["job"]["priority"] == 0
    assert events[2].event_metadata["allowed_tool_count"] == 0
    assert events[3].event_metadata["model"] == "gpt-4.1"
    assert events[4].event_metadata["runtime_event_count"] == 0


def test_worker_fails_closed_without_model_provider_credential() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, with_default_provider=False)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    run = orchestration.create_queued_run_for_task(task)
    orchestration.enqueue_run(run, requested_by_user_id=user.id)
    session.commit()

    handled = consume_once(
        queue,
        WorkerJobHandler(session, queue, agent_runner=ExplodingAgentRunner()).handle,
    )

    stored_run = session.get(AgentRun, run.id)
    stored_task = session.get(Task, task.id)
    event_types = session.scalars(
        select(RunEvent.event_type)
        .where(RunEvent.agent_run_id == run.id)
        .order_by(RunEvent.sequence)
    ).all()

    assert handled is True
    assert stored_run is not None
    assert stored_run.status == RunStatus.FAILED.value
    assert stored_task is not None
    assert stored_task.status == TaskStatus.FAILED.value
    assert "model.request_started" not in event_types
    assert "model_provider.unavailable" in event_types


def test_run_activity_maps_precise_execution_lifecycle_events() -> None:
    assert activity_phase("running", "run.claimed") == "worker_claimed"
    assert activity_phase("running", "run.context_built") == "context_ready"
    assert activity_phase("running", "model.request_started") == "model_running"
    assert activity_phase("running", "model.response_received") == "model_processing"
    assert activity_phase("running", "model.request_failed") == "model_failed"


def test_completed_run_auto_capture_writes_workspace_memory_when_enabled() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer token=agent-name-secret",
        role="writer",
        instructions="Write clearly.",
        memory_policy={"auto_capture": True},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft launch note token=task-title-secret",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={"request_metadata": {"api_key": "should-not-be-captured"}},
    )
    session.add(run)
    session.flush()

    RunOrchestrationService(session)._mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete with token=memory-secret."),
        requested_by_user_id=user.id,
    )

    entry = session.scalar(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.source_type == "agent_run",
            WorkspaceMemoryEntry.source_id == str(run.id),
            WorkspaceMemoryEntry.entry_type == "agent_run_summary",
        )
    )
    assert entry is not None
    assert entry.created_by_agent_profile_id == agent.id
    assert entry.created_by_agent_run_id == run.id
    assert entry.content == "Launch note complete with [redacted]"
    assert "memory-secret" not in entry.content
    assert entry.memory_metadata["agent"]["profile_id"] == str(agent.id)
    assert entry.memory_metadata["task"]["id"] == str(task.id)
    serialized_metadata = json.dumps(entry.memory_metadata)
    assert "api_key" not in serialized_metadata
    assert "agent-name-secret" not in serialized_metadata
    assert "task-title-secret" not in serialized_metadata


def test_completed_run_auto_capture_disabled_by_default() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write clearly.",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft launch note",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add(run)
    session.flush()

    RunOrchestrationService(session)._mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete."),
        requested_by_user_id=user.id,
    )

    assert session.scalar(select(WorkspaceMemoryEntry)) is None


def test_completed_run_auto_capture_is_idempotent_for_same_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write clearly.",
        memory_policy={"auto_capture": True},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft launch note",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add(run)
    session.flush()
    service = RunOrchestrationService(session)

    service._mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete."),
        requested_by_user_id=user.id,
    )
    run.status = RunStatus.RUNNING.value
    session.flush()
    service._mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete again."),
        requested_by_user_id=user.id,
    )

    assert session.scalar(select(func.count(WorkspaceMemoryEntry.id))) == 1


def test_completed_run_auto_compacts_existing_persistent_session() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write clearly.",
        memory_policy={
            "auto_compact_enabled": True,
            "session_max_items": 3,
            "session_keep_recent_items": 1,
            "summary_role": "system",
        },
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft launch note",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add(run)
    session.flush()
    persistent_session = PersistentAgentSession(
        workspace_id=workspace.id,
        session_key=f"{workspace.id}:task_agent:{task.id}:{agent.id}",
        scope_type="task_agent",
        scope_id=f"{task.id}:{agent.id}",
        agent_profile_id=agent.id,
        task_id=task.id,
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    for sequence, content in enumerate(["one", "two", "three", "four"], start=1):
        session.add(
            PersistentAgentSessionItem(
                workspace_id=workspace.id,
                persistent_session_id=persistent_session.id,
                sequence=sequence,
                item={"role": "user", "content": content},
            )
        )
    session.flush()

    RunOrchestrationService(session)._mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete."),
        requested_by_user_id=user.id,
    )

    items = session.scalars(
        select(PersistentAgentSessionItem)
        .where(PersistentAgentSessionItem.persistent_session_id == persistent_session.id)
        .order_by(PersistentAgentSessionItem.sequence)
    ).all()
    assert [item.sequence for item in items] == [1, 2]
    assert items[0].item["role"] == "system"
    assert "folded_items: 3" in str(items[0].item["content"])
    assert items[1].item["content"] == "four"


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

    handler = WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner())
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
    assert [step.result_summary for step in steps] == ["deterministic_run_completed"] * 4
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
    assert task.final_output["final_output"] == "deterministic_run_completed"
    assert task.final_output["team_orchestration"] == {
        "steps": [
                {
                    "task_step_id": str(step.id),
                    "title": step.title,
                    "status": "completed",
                    "work_package_id": step.work_package_id,
                    "required_role": step.required_role,
                    "agent_profile_id": str(step.assigned_agent_profile_id),
                    "result_summary": "deterministic_run_completed",
                }
            for step in steps
        ]
    }


def test_team_task_e2e_uses_runtime_space_queue_and_releases_reservations() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {"scheduler": {"max_active_runs": 3}}
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Market Team Space",
        scope="team",
        policy={"runtime_modes": ["docker"], "resource_requirements": {"cpu": 1}},
    )
    session.add(runtime_space)
    session.flush()
    session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            quota_key="active_runs",
            limit_value=3,
            reserved_value=0,
            unit="count",
        )
    )
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
        runtime_space_id=runtime_space.id,
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
        runtime_space_id=runtime_space.id,
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
    assert first_run is not None
    orchestration.enqueue_run(first_run, requested_by_user_id=user.id)
    session.commit()

    handler = WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner())
    handled = 0
    while consume_once(queue, handler.handle):
        handled += 1

    session.refresh(task)
    quota = session.scalar(
        select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.runtime_space_id == runtime_space.id)
    )
    reservations = session.scalars(
        select(RuntimeSpaceReservation).where(
            RuntimeSpaceReservation.runtime_space_id == runtime_space.id
        )
    ).all()
    runs = session.scalars(
        select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.created_at.asc())
    ).all()
    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.sequence)
    ).all()

    assert handled == 4
    assert task.status == TaskStatus.COMPLETED.value
    assert queue.count_queued(workspace_id=workspace.id) == 0
    assert quota is not None
    assert quota.reserved_value == 0
    assert len(reservations) == 4
    assert {reservation.status for reservation in reservations} == {"released"}
    assert all(run.runtime_space_id == runtime_space.id for run in runs)
    assert all(run.status == RunStatus.COMPLETED.value for run in runs)
    assert task.final_output is not None
    assert task.final_output["final_output"] == "deterministic_run_completed"
    message_types = [message.message_type for message in messages]
    assert "planning.completed" in message_types
    step_messages = [
        message for message in messages if message.message_type.startswith("step.")
    ]
    assert [message.message_type for message in step_messages] == [
        "step.started",
        "step.completed",
        "step.started",
        "step.completed",
        "step.started",
        "step.completed",
        "step.started",
        "step.completed",
    ]
    assert messages[-1].message_type == "pm.acceptance_decision"
    assert messages[-1].payload["decision"] == "approved"


def test_runtime_space_reserves_multi_resource_capacity_for_team_steps() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Build Space",
        scope="team",
        policy={
            "runtime_modes": ["docker"],
            "reservation_usage": {"docker_runtimes": 1, "storage_mb": 256},
            "resource_requirements": {"cpu": 2, "memory_mb": 1024},
        },
    )
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        instructions="Plan and review.",
        runtime_policy={"reservation_usage": {"artifact_mb": 50}},
    )
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        instructions="Build the feature.",
        runtime_policy={
            "resource_requirements": {"memory_mb": 1536},
            "reservation_usage": {"artifact_mb": 50},
        },
    )
    session.add_all([runtime_space, manager, developer])
    session.flush()
    quotas = [
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            quota_key=quota_key,
            limit_value=limit_value,
            reserved_value=0,
            unit=unit,
        )
        for quota_key, limit_value, unit in [
            ("active_runs", 2, "count"),
            ("docker_runtimes", 2, "count"),
            ("cpu", 4, "cores"),
            ("memory_mb", 2048, "mb"),
            ("storage_mb", 1024, "mb"),
            ("artifact_mb", 100, "mb"),
        ]
    ]
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Build Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        runtime_space_id=runtime_space.id,
    )
    session.add_all([*quotas, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=developer.id,
            team_role="Developer",
            order_index=0,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        runtime_space_id=runtime_space.id,
        title="Build feature",
        input={
            "work_packages": [
                {
                    "package_id": "build",
                    "title": "Build",
                    "required_role": "Developer",
                    "resource_requirements": {"storage_mb": 512},
                    "reservation_usage": {"self_hosted_jobs": 1},
                }
            ]
        },
    )
    session.add(task)
    session.flush()
    task.project_plan = {
        "plan_version": 1,
        "work_packages": [
            {
                "package_id": "manager-planning",
                "title": "Manager planning",
                "required_role": "manager",
                "assigned_agent_profile_id": str(manager.id),
                "dependencies": {},
            },
            {
                "package_id": "build",
                "title": "Build",
                "required_role": "Developer",
                "assigned_agent_profile_id": str(developer.id),
                "dependencies": {
                    "after_step_ids": [],
                    "resource_requirements": {"storage_mb": 512},
                    "reservation_usage": {"self_hosted_jobs": 1},
                },
            },
            {
                "package_id": "manager-summary",
                "title": "Manager summary",
                "required_role": "manager",
                "assigned_agent_profile_id": str(manager.id),
                "dependencies": {},
            },
        ],
    }
    build_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        runtime_space_id=runtime_space.id,
        work_package_id="build",
        required_role="Developer",
        title="Build",
        status="queued",
        order_index=1,
        dependencies={
            "resource_requirements": {"storage_mb": 512},
            "reservation_usage": {"self_hosted_jobs": 1},
        },
    )
    session.add(build_step)
    session.flush()

    orchestration = RunOrchestrationService(session)
    run = orchestration._create_reserved_run_for_step(task, build_step)

    assert run is not None
    assert run.runtime_space_id == runtime_space.id
    reservations = session.scalars(
        select(RuntimeSpaceReservation).where(
            RuntimeSpaceReservation.runtime_space_id == runtime_space.id
        )
    ).all()
    assert len(reservations) == 1
    assert reservations[0].resource_usage == {
        "active_runs": 1,
        "docker_runtimes": 1,
        "storage_mb": 512,
        "cpu": 2,
        "memory_mb": 1536,
        "artifact_mb": 50,
        "self_hosted_jobs": 1,
    }
    quota_by_key = {
        quota.quota_key: quota
        for quota in session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.runtime_space_id == runtime_space.id
            )
        ).all()
    }
    assert quota_by_key["active_runs"].reserved_value == 1
    assert quota_by_key["docker_runtimes"].reserved_value == 1
    assert quota_by_key["cpu"].reserved_value == 2
    assert quota_by_key["memory_mb"].reserved_value == 1536
    assert quota_by_key["storage_mb"].reserved_value == 512
    assert quota_by_key["artifact_mb"].reserved_value == 50
    assert "self_hosted_jobs" not in quota_by_key

    orchestration._release_runtime_space_reservations(
        run,
        released_at=datetime.now(UTC),
    )

    for quota in quota_by_key.values():
        assert quota.reserved_value == 0
    assert reservations[0].status == "released"


def test_runtime_space_blocks_step_when_multi_resource_quota_exceeded() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Small Space",
        scope="team",
        policy={"resource_requirements": {"memory_mb": 1024}},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Heavy Agent",
        role="developer",
        instructions="Use memory.",
        runtime_policy={"resource_requirements": {"memory_mb": 4096}},
    )
    session.add_all([runtime_space, agent])
    session.flush()
    quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="memory_mb",
        limit_value=2048,
        reserved_value=0,
        unit="mb",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        runtime_space_id=runtime_space.id,
        title="Heavy task",
    )
    session.add_all([quota, task])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        runtime_space_id=runtime_space.id,
        title="Heavy step",
        status="queued",
        order_index=0,
    )
    session.add(step)
    session.flush()

    run = RunOrchestrationService(session)._create_reserved_run_for_step(task, step)

    assert run is None
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:memory_mb"
    session.refresh(quota)
    assert quota.reserved_value == 0


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

    handler = WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner())
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


def test_workspace_run_quota_limits_parallel_specialist_scheduling() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {"scheduler": {"max_active_runs": 1}}
    manager = AgentProfile(workspace_id=workspace.id, name="Manager", role="manager")
    researcher = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    analyst = AgentProfile(workspace_id=workspace.id, name="Analyst", role="analyst")
    session.add_all([manager, researcher, analyst])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Research Team",
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

    handler = WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner())
    assert consume_once(queue, handler.handle) is True
    session.expire_all()

    queued_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.task_id == task.id,
            AgentRun.status == RunStatus.QUEUED.value,
        )
    ).all()
    queued_step_ids = {run.task_step_id for run in queued_runs}
    specialist_steps = session.scalars(
        select(TaskStep).where(
            TaskStep.task_id == task.id,
            TaskStep.work_package_id.in_(["Research-1", "Analysis-2"]),
        )
    ).all()

    assert queue.count_queued(workspace_id=workspace.id) == 1
    assert len(queued_runs) == 1
    assert any(step.id in queued_step_ids for step in specialist_steps)
    assert any(
        step.dependencies.get("blocked_reason") == "workspace_run_quota_exceeded"
        for step in specialist_steps
        if step.id not in queued_step_ids
    )


def test_team_scheduler_policy_limits_team_steps_without_relaxing_workspace_policy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {"scheduler": {"max_active_runs": 3}}
    manager = AgentProfile(workspace_id=workspace.id, name="Manager", role="manager")
    developer = AgentProfile(workspace_id=workspace.id, name="Developer", role="developer")
    session.add_all([manager, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Policy Team",
        team_type="software",
        manager_agent_profile_id=manager.id,
        default_task_policy={"scheduler": {"max_runs_to_start_per_tick": 1}},
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=developer.id,
            team_role="developer",
            order_index=1,
        )
    )
    first_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="First team task",
        priority=10,
        status=TaskStatus.QUEUED.value,
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Second team task",
        priority=9,
        status=TaskStatus.QUEUED.value,
    )
    session.add_all([first_task, second_task])
    session.flush()
    first_step = TaskStep(
        workspace_id=workspace.id,
        task_id=first_task.id,
        assigned_agent_profile_id=developer.id,
        title="First build",
        status="queued",
        order_index=0,
    )
    second_step = TaskStep(
        workspace_id=workspace.id,
        task_id=second_task.id,
        assigned_agent_profile_id=developer.id,
        title="Second build",
        status="queued",
        order_index=0,
    )
    session.add_all([first_step, second_step])
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_team_steps(
        workspace_id=workspace.id,
        team_id=team.id,
        requested_by_user_id=user.id,
    )

    assert [run.task_id for run in runs] == [first_task.id]
    assert queue.count_queued(workspace_id=workspace.id) == 1
    assert second_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"


def test_team_scheduler_blocks_step_when_model_provider_unavailable() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Blocked provider",
        provider="openai-compatible",
        api_key="sk-scheduler-provider-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat-completions"},
    )
    credential.health_status = "unhealthy"
    credential.failure_count = 3
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
        model_settings={"model_api": "response"},
    )
    session.add(developer)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Provider blocked team",
        team_type="software",
        manager_agent_profile_id=developer.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=developer.id,
            team_role="developer",
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Blocked provider task",
        priority=10,
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        title="Build with blocked provider",
        status="queued",
        order_index=0,
    )
    session.add(step)
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_team_steps(
        workspace_id=workspace.id,
        team_id=team.id,
        requested_by_user_id=user.id,
    )

    assert runs == []
    assert queue.count_queued(workspace_id=workspace.id) == 0
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "model_provider_unavailable"
    assert step.dependencies["blocked_details"]["error_type"] == "ValueError"
    assert "unavailable" in step.dependencies["blocked_details"]["message"]
    blocked_provider = step.dependencies["blocked_details"]["model_provider"]
    assert blocked_provider["source"] == "agent_override"
    assert blocked_provider["agent_profile_id"] == str(developer.id)
    assert blocked_provider["agent_model"] == "workspace-default"
    assert blocked_provider["credential_id"] == str(credential.id)
    assert blocked_provider["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert blocked_provider["credential_status"] == "active"
    assert blocked_provider["credential_health_status"] == "unhealthy"
    assert blocked_provider["budget_exhausted"] is False
    assert blocked_provider["provider"] == "openai-compatible"
    assert blocked_provider["default_model"] == "gpt-4.1-mini"
    assert blocked_provider["model_api"] == "responses"
    assert session.scalars(select(AgentRun)).all() == []
    assert "sk-scheduler-provider-blocked" not in str(step.dependencies)
    assert "provider.example.test/v1" not in str(step.dependencies)


def test_team_scheduler_releases_reservations_when_model_provider_unavailable() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace_quota = WorkspaceQuota(
        workspace_id=workspace.id,
        quota_key="active_runs",
        limit_value=1,
        reserved_value=0,
        unit="count",
    )
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Provider blocked runtime space",
        scope="team",
    )
    session.add_all([workspace_quota, runtime_space])
    session.flush()
    runtime_quota = RuntimeSpaceQuota(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        quota_key="active_runs",
        limit_value=1,
        reserved_value=0,
        unit="count",
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Blocked provider",
        provider="openai-compatible",
        api_key="sk-scheduler-release-blocked",
        default_model="gpt-4.1-mini",
        base_url="https://provider.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat-completions"},
    )
    credential.health_status = "unhealthy"
    credential.failure_count = 3
    developer = AgentProfile(
        workspace_id=workspace.id,
        name="Developer",
        role="developer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    session.add_all([runtime_quota, developer])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Provider blocked team",
        team_type="software",
        manager_agent_profile_id=developer.id,
        runtime_space_id=runtime_space.id,
    )
    session.add(team)
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=developer.id,
            team_role="developer",
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        runtime_space_id=runtime_space.id,
        title="Blocked provider task",
        priority=10,
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=developer.id,
        runtime_space_id=runtime_space.id,
        title="Build with blocked provider",
        status="queued",
        order_index=0,
    )
    session.add(step)
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_team_steps(
        workspace_id=workspace.id,
        team_id=team.id,
        requested_by_user_id=user.id,
    )

    workspace_reservations = session.scalars(
        select(WorkspaceReservation).where(
            WorkspaceReservation.workspace_id == workspace.id,
        )
    ).all()
    runtime_reservations = session.scalars(
        select(RuntimeSpaceReservation).where(
            RuntimeSpaceReservation.runtime_space_id == runtime_space.id,
        )
    ).all()

    assert runs == []
    assert queue.count_queued(workspace_id=workspace.id) == 0
    assert session.scalars(select(AgentRun)).all() == []
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "model_provider_unavailable"
    assert step.dependencies["blocked_details"]["error_type"] == "ValueError"
    assert "unavailable" in step.dependencies["blocked_details"]["message"]
    blocked_provider = step.dependencies["blocked_details"]["model_provider"]
    assert blocked_provider["source"] == "agent_override"
    assert blocked_provider["agent_profile_id"] == str(developer.id)
    assert blocked_provider["agent_model"] == "workspace-default"
    assert blocked_provider["credential_id"] == str(credential.id)
    assert blocked_provider["credential_reference"] == (
        f"model_provider_credentials:{credential.id}"
    )
    assert blocked_provider["credential_status"] == "active"
    assert blocked_provider["credential_health_status"] == "unhealthy"
    assert blocked_provider["budget_exhausted"] is False
    assert blocked_provider["model_api"] == "chat_completions"
    assert "sk-scheduler-release-blocked" not in str(step.dependencies)
    assert "provider.example.test/v1" not in str(step.dependencies)
    assert workspace_quota.reserved_value == 0
    assert runtime_quota.reserved_value == 0
    assert len(workspace_reservations) == 1
    assert len(runtime_reservations) == 1
    assert workspace_reservations[0].status == "released"
    assert runtime_reservations[0].status == "released"
    assert workspace_reservations[0].agent_run_id is None
    assert runtime_reservations[0].agent_run_id is None


def test_workspace_scheduler_starts_higher_priority_task_first() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {"scheduler": {"max_active_runs": 1}}
    low_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Low priority",
        priority=1,
        status=TaskStatus.QUEUED.value,
    )
    high_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="High priority",
        priority=10,
        status=TaskStatus.QUEUED.value,
    )
    session.add_all([low_task, high_task])
    session.flush()
    low_step = TaskStep(
        workspace_id=workspace.id,
        task_id=low_task.id,
        title="Low step",
        status="queued",
        order_index=0,
    )
    high_step = TaskStep(
        workspace_id=workspace.id,
        task_id=high_task.id,
        title="High step",
        status="queued",
        order_index=0,
    )
    session.add_all([low_step, high_step])
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_workspace_steps(
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
    )

    assert [run.task_id for run in runs] == [high_task.id]
    assert queue.count_queued(workspace_id=workspace.id) == 1
    queued_job = queue.dequeue()
    assert queued_job is not None
    assert queued_job.priority == 10
    assert queued_job.routing["priority"] == 10
    assert low_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"


def test_workspace_scheduler_boosts_starved_lower_priority_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {
        "scheduler": {
            "max_active_runs": 1,
            "starvation_boost_after_seconds": 60,
        }
    }
    low_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Old low priority",
        priority=1,
        status=TaskStatus.QUEUED.value,
    )
    high_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Fresh high priority",
        priority=10,
        status=TaskStatus.QUEUED.value,
    )
    session.add_all([low_task, high_task])
    session.flush()
    low_step = TaskStep(
        workspace_id=workspace.id,
        task_id=low_task.id,
        title="Old low step",
        status="queued",
        order_index=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    high_step = TaskStep(
        workspace_id=workspace.id,
        task_id=high_task.id,
        title="Fresh high step",
        status="queued",
        order_index=0,
        created_at=datetime.now(UTC),
    )
    session.add_all([low_step, high_step])
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_workspace_steps(
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
    )

    assert [run.task_id for run in runs] == [low_task.id]
    assert queue.count_queued(workspace_id=workspace.id) == 1
    queued_job = queue.dequeue()
    assert queued_job is not None
    assert queued_job.priority == 1
    assert high_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"
    assert high_step.dependencies["priority_score"] == 10


def test_workspace_scheduler_blocks_steps_over_resource_limits() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    workspace.settings = {
        "scheduler": {
            "max_active_runs": 3,
            "resource_limits": {
                "cpu": 4,
                "memory_mb": 2048,
                "storage_mb": 1024,
                "self_hosted_jobs": 1,
            },
        }
    }
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Resource limited task",
        priority=5,
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    allowed_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Allowed package",
        status="queued",
        order_index=0,
        dependencies={
            "resource_requirements": {
                "cpu": 2,
                "memory_mb": 1024,
                "storage_mb": 512,
                "self_hosted_jobs": 1,
            }
        },
    )
    blocked_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Blocked package",
        status="queued",
        order_index=1,
        dependencies={
            "resource_requirements": {
                "cpu": 3,
                "memory_mb": 1536,
                "storage_mb": 768,
                "self_hosted_jobs": 1,
            }
        },
    )
    session.add_all([allowed_step, blocked_step])
    session.flush()
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )

    runs = RunOrchestrationService(session, queue).schedule_workspace_steps(
        workspace_id=workspace.id,
        requested_by_user_id=user.id,
    )

    assert [run.task_step_id for run in runs] == [allowed_step.id]
    assert queue.count_queued(workspace_id=workspace.id) == 1
    assert blocked_step.dependencies["blocked_reason"] == "workspace_resource_quota_exceeded"
    assert blocked_step.dependencies["blocked_resource_keys"] == [
        "cpu",
        "memory_mb",
        "storage_mb",
        "self_hosted_jobs",
    ]


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


def test_pm_summary_missing_work_matches_team_member_and_queues_follow_up() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task, summary_step, manager = _seed_summary_ready_task(session, user.id, workspace.id)
    designer = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design interfaces.",
        model="designer-model",
    )
    session.add(designer)
    session.flush()
    task.team_snapshot = {
        "team": {"manager_agent_profile_id": str(manager.id)},
        "members": [
            {
                "id": str(uuid4()),
                "agent_profile_id": str(designer.id),
                "team_role": "designer",
                "skill_weights": {"ui": 1.0},
                "accepts_tasks": True,
            }
        ],
    }
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
            "decision": "add_missing_work",
            "summary": "Needs an extra UI pass.",
            "missing_work_packages": [
                {
                    "package_id": "ui-polish",
                    "title": "UI polish",
                    "required_role": "designer",
                    "required_skills": ["ui"],
                    "instruction": "Polish the checkout screen.",
                }
            ],
        }
    )
    orchestration = RunOrchestrationService(session)
    orchestration._mark_run_started(run)
    orchestration._mark_run_completed(run, output, requested_by_user_id=user.id)
    session.flush()

    session.refresh(task)
    follow_up_step = session.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task.id,
            TaskStep.work_package_id == "ui-polish",
        )
    )
    review_step = session.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task.id,
            TaskStep.work_package_id == "manager-summary-revision-1",
        )
    )
    follow_up_run = session.scalar(
        select(AgentRun).where(
            AgentRun.task_id == task.id,
            AgentRun.task_step_id == follow_up_step.id,
        )
    )

    assert task.status == TaskStatus.RUNNING.value
    assert task.final_output["pm_acceptance"]["decision"] == "add_missing_work"
    assert follow_up_step is not None
    assert follow_up_step.assigned_agent_profile_id == designer.id
    assert follow_up_step.required_role == "designer"
    assert follow_up_step.required_skills == ["ui"]
    assert follow_up_step.description == "Polish the checkout screen."
    assert review_step is not None
    assert review_step.dependencies["after_step_ids"] == [str(follow_up_step.id)]
    assert follow_up_run is not None
    assert follow_up_run.agent_profile_id == designer.id


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


def test_run_authorization_snapshot_freezes_agent_tool_policy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        tool_policy={"allowed_tools": ["generate_image"]},
        runtime_policy={
            "provider": "docker",
            "network": "disabled",
            "mcp": {"timeout_seconds": 45, "max_output_bytes": 512_000},
        },
        approval_policy={"required_tools": ["write_artifact"]},
    )
    team = AgentTeam(workspace_id=workspace.id, name="Design Team", team_type="design")
    session.add_all([agent, team])
    session.flush()
    skill = Skill(
        key="poster-maker",
        name="Poster Maker",
        version="1.0.0",
        capability_keys=["image.generate"],
        manifest={"tools": ["generate_image"]},
        visibility="public",
    )
    session.add(skill)
    session.flush()
    install = WorkspaceSkillInstall(
        workspace_id=workspace.id,
        skill_id=skill.id,
        installed_by_user_id=user.id,
        installed_key=skill.key,
        installed_name=skill.name,
        installed_version=skill.version,
        installed_description=skill.description,
        installed_capability_keys=skill.capability_keys,
        installed_manifest=skill.manifest,
        source_visibility=skill.visibility,
        source_checksum="sha256:installed",
    )
    session.add(install)
    session.flush()
    server = McpServer(
        workspace_id=workspace.id,
        name="Image MCP",
        server_type="http",
        connection={"url": "https://mcp.example.test/rpc"},
    )
    session.add(server)
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        capability_key="image.generate",
        requires_approval=True,
        risk_level="medium",
        policy={"timeout_seconds": 12},
    )
    credential_ref = McpCredentialReference(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        name="image-key",
        provider="hosted",
        external_ref="vault://do-not-freeze",
        encrypted_secret_payload="encrypted-secret",
        secret_fingerprint="fp-image",
        encryption_key_id="test-key",
        scopes=["image.generate"],
    )
    session.add_all([allow, credential_ref])
    session.flush()
    agent.skills = {"installed_skill_ids": [str(install.id), "not-a-uuid"]}
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="designer",
        skill_weights={"ui": 1.0},
    )
    session.add(member)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        team_snapshot={
            "team": {"id": str(team.id), "name": team.name},
            "members": [
                {
                    "id": str(member.id),
                    "agent_profile_id": str(agent.id),
                    "team_role": "designer",
                    "skill_weights": {"ui": 1.0},
                    "accepts_tasks": True,
                }
            ],
            "agents": [],
        },
        input={
            "work_packages": [
                {
                    "package_id": "visual-design",
                    "title": "Visual design",
                    "required_role": "designer",
                    "required_skills": ["ui"],
                    "expected_artifacts": ["image"],
                }
            ]
        },
        title="Design launch image",
    )
    session.add(task)
    session.flush()

    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    snapshot = run.input["authorization_snapshot"]
    agent.tool_policy = {"allowed_tools": ["delete_workspace_file"]}
    session.commit()

    request = RunOrchestrationService(session)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="snapshot-policy",
        ),
    )

    assert snapshot["allowed_tools"] == ["generate_image"]
    assert snapshot["installed_skills"] == [
        {
            "install_id": str(install.id),
            "source_skill_id": str(skill.id),
            "installed_key": "poster-maker",
            "installed_name": "Poster Maker",
            "installed_version": "1.0.0",
            "installed_capability_keys": ["image.generate"],
            "source_checksum": "sha256:installed",
            "source_visibility": "public",
            "mcp_tools": [
                {
                    "allowlist_id": str(allow.id),
                    "mcp_server_id": str(server.id),
                    "mcp_server_name": "Image MCP",
                    "server_type": "http",
                    "tool_name": "generate_image",
                    "capability_key": "image.generate",
                    "requires_approval": True,
                    "risk_level": "medium",
                    "policy": {"timeout_seconds": 12},
                    "credential_reference_ids": [str(credential_ref.id)],
                    "credential_references": [
                        {
                            "credential_reference_id": str(credential_ref.id),
                            "mcp_server_id": str(server.id),
                            "name": "image-key",
                            "provider": "hosted",
                            "secret_fingerprint": "fp-image",
                            "encryption_key_id": "test-key",
                            "scopes": ["image.generate"],
                        }
                    ],
                }
            ],
            "mcp_credential_references": [
                {
                    "credential_reference_id": str(credential_ref.id),
                    "mcp_server_id": str(server.id),
                    "name": "image-key",
                    "provider": "hosted",
                    "secret_fingerprint": "fp-image",
                    "encryption_key_id": "test-key",
                    "scopes": ["image.generate"],
                }
            ],
        }
    ]
    assert "encrypted-secret" not in str(snapshot["installed_skills"])
    assert "vault://do-not-freeze" not in str(snapshot["installed_skills"])
    assert snapshot["runtime_policy"] == {
        "provider": "docker",
        "network": "disabled",
        "mcp": {
            "network_mode": "disabled",
            "timeout_seconds": 45,
            "max_input_bytes": 64_000,
            "max_output_bytes": 512_000,
        },
    }
    assert snapshot["approval_policy"] == {"required_tools": ["write_artifact"]}
    assert request.context.allowed_tools == ("generate_image",)
    assert request.tool_executor is not None
    assert request.context.metadata["authorization_snapshot_version"] == 1


def test_resumed_run_carries_completed_self_hosted_tool_continuations() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Create image",
        description="Use the render result.",
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": [],
            },
            "pending_tool_results": [
                {
                    "tool_name": "generate_image",
                    "status": "completed",
                    "response": {"asset_id": "img_123"},
                    "request": {"prompt": "do not include this original prompt"},
                }
            ],
        },
    )
    session.add(run)
    session.commit()

    request = RunOrchestrationService(session)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="resumed-tool-result",
        ),
    )

    assert request.continuations[0].tool_name == "generate_image"
    assert request.continuations[0].status == "completed"
    assert request.continuations[0].result == {"asset_id": "img_123"}
    assert request.context.metadata["tool_continuations"] == [
        {
            "tool_name": "generate_image",
            "status": "completed",
            "metadata": {},
        }
    ]
    assert request.tracing is not None
    assert request.tracing.workflow_name == "chaincloud.agent_run"
    assert request.tracing.group_id == f"task:{task.id}"
    assert request.tracing.metadata["tool_continuations"] == [
        {
            "tool_name": "generate_image",
            "status": "completed",
            "metadata": {},
        }
    ]
    assert "Completed runtime tool results" not in request.input_text
    assert "img_123" not in request.input_text
    assert "do not include this original prompt" not in request.input_text


def test_queued_team_run_freezes_model_provider_snapshot_without_secret() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="unit-test-secret",
        credential_encryption_key_id="test-key",
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Private Router",
        provider="openai-compatible",
        api_key="sk-never-store-in-run",
        default_model="router/default",
        base_url="https://llm.example.test/v1",
        is_default=True,
        budget_metadata={"model_api": "chat_completions"},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        model="workspace-default",
        model_provider_credential_id=credential.id,
    )
    team = AgentTeam(workspace_id=workspace.id, name="Writing Team", team_type="writing")
    session.add_all([agent, team])
    session.flush()
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="writer",
    )
    session.add(member)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        team_snapshot={
            "team": {"id": str(team.id), "name": team.name},
            "members": [
                {
                    "id": str(member.id),
                    "agent_profile_id": str(agent.id),
                    "team_role": "writer",
                    "accepts_tasks": True,
                }
            ],
            "agents": [],
        },
        input={"work_packages": [{"package_id": "draft", "title": "Draft"}]},
        title="Draft chapter",
    )
    session.add(task)
    session.flush()

    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    snapshot = run.input["authorization_snapshot"]["model_provider"]
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()

    assert run.model == "router/default"
    assert snapshot["source"] == "agent_override"
    assert snapshot["selected_model"] == "router/default"
    assert snapshot["credential_id"] == str(credential.id)
    assert snapshot["credential_reference"] == f"model_provider_credentials:{credential.id}"
    assert snapshot["provider"] == "openai-compatible"
    assert snapshot["base_url_host"] == "llm.example.test"
    assert snapshot["base_url_configured"] is True
    assert snapshot["api_key_fingerprint"] == credential.api_key_fingerprint
    assert snapshot["model_api"] == "chat_completions"
    assert snapshot["model_capability"] == {
        "provider": "openai-compatible",
        "model": "*",
        "display_name": "OpenAI-compatible model",
        "capabilities": ["tools", "json_mode", "streaming"],
        "supports_tools": True,
        "supports_vision": False,
        "supports_json_mode": True,
        "supports_streaming": True,
        "context_window_tokens": None,
        "notes": "Actual support depends on the upstream gateway and selected model.",
    }
    assert snapshot["credential_status"] == "active"
    assert snapshot["credential_health_status"] == "unknown"
    assert snapshot["failure_count"] == 0
    assert snapshot["budget_exhausted"] is False
    assert snapshot["last_failure_code"] is None
    assert "api_key" not in snapshot
    assert "base_url" not in snapshot
    assert [event.event_type for event in events] == ["model_provider.resolved"]
    assert events[0].event_metadata == {"model_provider": snapshot}

    run.input = {
        **run.input,
        "authorization_snapshot": {
            **run.input["authorization_snapshot"],
            "model_provider": {
                **snapshot,
                "model_api": "chat-completions",
            },
        },
    }
    credential.budget_metadata = {"model_api": "responses"}
    session.flush([credential])
    request = RunOrchestrationService(session, settings=settings)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="frozen-model-api",
        ),
    )

    assert request.model_api == "chat_completions"


def test_queued_team_run_uses_frozen_agent_model_provider_protocol() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="unit-test-secret",
        credential_encryption_key_id="test-key",
    )
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Router",
        provider="openai-compatible",
        api_key="sk-never-freeze",
        default_model="router/default",
        base_url="https://llm.example.test/v1",
        is_default=True,
        budget_metadata={"model_api": "responses"},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        model="db-model",
        model_provider_credential_id=credential.id,
        model_settings={"model_api": "responses"},
    )
    team = AgentTeam(workspace_id=workspace.id, name="Writing Team", team_type="writing")
    session.add_all([agent, team])
    session.flush()
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="writer",
    )
    session.add(member)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        team_snapshot={
            "snapshot_version": 1,
            "team": {"id": str(team.id), "name": team.name},
            "members": [
                {
                    "id": str(member.id),
                    "agent_profile_id": str(agent.id),
                    "team_role": "writer",
                    "accepts_tasks": True,
                    "agent": {
                        "id": str(agent.id),
                        "name": "Frozen Writer",
                        "role": "writer",
                        "model": "snapshot-model",
                        "model_provider_credential_id": str(credential.id),
                        "model_api": "chat-completions",
                    },
                }
            ],
            "agents": [],
        },
        project_plan={
            "plan_version": 1,
            "work_packages": [
                {
                    "package_id": "draft",
                    "title": "Draft",
                    "assigned_agent_profile_id": str(agent.id),
                    "required_role": "writer",
                }
            ],
        },
        title="Draft chapter",
    )
    session.add(task)
    session.flush()

    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    snapshot = run.input["authorization_snapshot"]["model_provider"]
    request = RunOrchestrationService(session, settings=settings)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="frozen-agent-provider-protocol",
        ),
    )

    assert run.model == "snapshot-model"
    assert snapshot["agent_model"] == "snapshot-model"
    assert snapshot["selected_model"] == "snapshot-model"
    assert snapshot["model_api"] == "chat_completions"
    assert request.model == "snapshot-model"
    assert request.model_api == "chat_completions"
    assert request.context.metadata["model_provider_model_api"] == "chat_completions"


def test_disabled_skill_install_is_not_in_future_run_snapshot() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        skills={},
    )
    team = AgentTeam(workspace_id=workspace.id, name="Writing Team", team_type="writing")
    skill = Skill(
        key="writer",
        name="Writer",
        version="1.0.0",
        manifest={"prompt": "v1"},
        visibility="public",
    )
    session.add_all([agent, team, skill])
    session.flush()
    install = WorkspaceSkillInstall(
        workspace_id=workspace.id,
        skill_id=skill.id,
        installed_by_user_id=user.id,
        installed_key=skill.key,
        installed_name=skill.name,
        installed_version=skill.version,
        installed_description=skill.description,
        installed_capability_keys=skill.capability_keys,
        installed_manifest=skill.manifest,
        source_visibility=skill.visibility,
        source_checksum="sha256:v1",
    )
    session.add(install)
    session.flush()
    agent.skills = {"installed_skill_ids": [str(install.id)]}
    member = AgentTeamMember(
        workspace_id=workspace.id,
        agent_team_id=team.id,
        agent_profile_id=agent.id,
        team_role="writer",
    )
    session.add(member)
    session.flush()

    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        team_snapshot={
            "team": {"id": str(team.id), "name": team.name},
            "members": [
                {
                    "id": str(member.id),
                    "agent_profile_id": str(agent.id),
                    "team_role": "writer",
                    "accepts_tasks": True,
                }
            ],
            "agents": [],
        },
        input={"work_packages": [{"package_id": "draft", "title": "Draft"}]},
        title="Draft chapter",
    )
    session.add(task)
    session.flush()

    service = RunOrchestrationService(session)
    first_run = service.create_queued_run_for_task(task)
    first_snapshot = first_run.input["authorization_snapshot"]
    first_run.status = RunStatus.COMPLETED.value
    install.status = "disabled"
    session.flush()
    step = session.get(TaskStep, first_run.task_step_id)
    assert step is not None
    second_run = service._create_run_for_step(task, step)
    second_snapshot = second_run.input["authorization_snapshot"]

    assert first_snapshot["installed_skills"][0]["source_checksum"] == "sha256:v1"
    assert second_snapshot["installed_skills"] == []


def test_agent_request_restores_provider_native_continuation_from_persistent_session() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        model="gpt-4.1",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft continued note",
        status=TaskStatus.QUEUED.value,
    )
    session.add_all([agent, task])
    session.flush()
    session.add(
        PersistentAgentSession(
            workspace_id=workspace.id,
            session_key=f"{workspace.id}:task_agent:{task.id}:{agent.id}",
            scope_type="task_agent",
            scope_id=f"{task.id}:{agent.id}",
            agent_profile_id=agent.id,
            task_id=task.id,
            openai_conversation_id="conv_existing",
        )
    )
    first_run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.COMPLETED.value,
        completed_at=datetime.now(UTC) - timedelta(minutes=1),
        output={
            "final_output": "Earlier result",
            "raw_output": {
                "sdk_continuation": {
                    "last_response_id": "resp_previous",
                    "resume_input": [{"role": "user", "content": "continue"}],
                }
            },
        },
    )
    second_run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add_all([first_run, second_run])
    session.commit()

    class CapturingRunner:
        def __init__(self) -> None:
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            return AgentRunResult(final_output="continued")

    runner = CapturingRunner()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=second_run.id,
        requested_by_user_id=user.id,
        idempotency_key="provider-native-continuation",
    )

    RunOrchestrationService(session, agent_runner=runner).run_agent_sync(job)

    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.previous_response_id == "resp_previous"
    assert request.conversation_id == "conv_existing"
    assert request.context.metadata["previous_response_id"] == "resp_previous"
    assert request.context.metadata["conversation_id"] == "conv_existing"


def test_completed_run_updates_persistent_session_conversation_id() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        model="gpt-4.1",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft continued note",
        status=TaskStatus.RUNNING.value,
    )
    session.add_all([agent, task])
    session.flush()
    persistent_session = PersistentAgentSession(
        workspace_id=workspace.id,
        session_key=f"{workspace.id}:task_agent:{task.id}:{agent.id}",
        scope_type="task_agent",
        scope_id=f"{task.id}:{agent.id}",
        agent_profile_id=agent.id,
        task_id=task.id,
        openai_conversation_id="conv_old",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.RUNNING.value,
        input={},
    )
    session.add_all([persistent_session, run])
    session.flush()

    RunOrchestrationService(session)._mark_run_completed(
        run,
        AgentRunResult(
            final_output="continued",
            raw_output={
                "sdk_continuation": {
                    "conversation_id": "conv_new",
                    "last_response_id": "resp_new",
                }
            },
        ),
        requested_by_user_id=user.id,
    )

    assert persistent_session.openai_conversation_id == "conv_new"


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

    handler = WorkerJobHandler(session, queue, agent_runner=DeterministicAgentRunner())
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


def test_invalid_project_plan_records_attempt_and_blocks_task_for_review() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Manager",
        role="manager",
        model="manager-model",
    )
    session.add(manager)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Build dashboard",
        agent_team_id=uuid4(),
        team_snapshot={
            "snapshot_version": 1,
            "team": {
                "id": str(uuid4()),
                "name": "Broken Team Snapshot",
                "manager_agent_profile_id": str(manager.id),
            },
            "members": [],
            "agents": [],
        },
        input={
            "work_packages": [
                {
                    "package_id": "build-ui",
                    "title": "Build UI",
                    "required_role": "frontend_engineer",
                },
                {
                    "package_id": "build-ui",
                    "title": "Build UI again",
                    "required_role": "frontend_engineer",
                }
            ]
        },
    )
    session.add(task)
    session.flush()

    run = RunOrchestrationService(session).create_queued_run_for_task(task)

    attempt = session.scalar(select(TaskPlanningAttempt))
    approval = session.scalar(select(Approval))
    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.sequence)
    ).all()

    assert run is None
    assert task.status == TaskStatus.BLOCKED.value
    assert task.project_plan is None
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.validation_errors == ["Duplicate work package id: build-ui"]
    assert approval is not None
    assert approval.approval_type == "task.plan_review"
    assert approval.payload["attempt_id"] == str(attempt.id)
    assert approval.payload["validation_errors"] == attempt.validation_errors
    assert [message.message_type for message in messages] == ["planning.failed"]


def test_worker_rejects_workspace_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
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
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
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
    request_failed_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "model.request_failed",
        )
    )
    assert request_failed_event is not None
    assert request_failed_event.event_metadata["reason"] == {
        "code": "RuntimeError",
        "message": "model failed",
        "retryable": True,
    }
    assert failed_event is not None


def test_worker_flushes_running_status_before_model_call() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()

    observed_statuses: list[str] = []

    class RefreshingRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            stored_run = session.get(AgentRun, request.context.run_id)
            assert stored_run is not None
            session.refresh(stored_run)
            observed_statuses.append(stored_run.status)
            return AgentRunResult(final_output="refreshed_status_completed")

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="flush-running-before-model",
    )

    WorkerJobHandler(session, agent_runner=RefreshingRunner()).handle(job)

    stored_run = session.get(AgentRun, run.id)
    assert observed_statuses == [RunStatus.RUNNING.value]
    assert stored_run is not None
    assert stored_run.status == RunStatus.COMPLETED.value


def test_worker_skips_cancelled_run_without_starting_model() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()
    assert run is not None

    RunOrchestrationService(session).cancel_task(
        workspace_id=workspace.id,
        task_id=task.id,
        actor_user_id=user.id,
    )

    class ExplodingRunner:
        async def run(self, request):
            raise AssertionError("cancelled run should not call the model")

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="cancelled-run",
    )

    WorkerJobHandler(session, agent_runner=ExplodingRunner()).handle(job)

    stored_run = session.get(AgentRun, run.id)
    event_types = session.scalars(
        select(RunEvent.event_type)
        .where(RunEvent.agent_run_id == run.id)
        .order_by(RunEvent.sequence.asc())
    ).all()

    assert stored_run is not None
    assert stored_run.status == RunStatus.CANCELLED.value
    assert "run.started" not in event_types


def test_worker_discards_model_result_when_run_cancelled_during_execution() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()
    assert run is not None

    class CancellingRunner:
        async def run(self, request):
            RunOrchestrationService(session).cancel_run(
                workspace_id=workspace.id,
                run_id=request.context.run_id,
                actor_user_id=user.id,
            )
            return AgentRunResult(final_output="should be discarded")

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="cancel-during-run",
    )

    WorkerJobHandler(session, agent_runner=CancellingRunner()).handle(job)

    stored_run = session.get(AgentRun, run.id)
    stored_task = session.get(Task, task.id)
    event_types = session.scalars(
        select(RunEvent.event_type)
        .where(RunEvent.agent_run_id == run.id)
        .order_by(RunEvent.sequence.asc())
    ).all()

    assert stored_run is not None
    assert stored_run.status == RunStatus.CANCELLED.value
    assert stored_run.output is None
    assert stored_task is not None
    assert stored_task.status == TaskStatus.CANCELLED.value
    assert "run.completed" not in event_types
    assert "run.result_discarded_after_cancel" in event_types


def test_worker_maps_runtime_events_to_sanitized_task_messages() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
    )
    session.add_all([task, agent])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Research",
        work_package_id="research-1",
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

    class EventfulRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            return AgentRunResult(
                final_output="done",
                events=(
                    AgentRuntimeEvent(
                        event_type="tool.called",
                        message="Searching docs",
                        payload={
                            "tool_name": "search",
                            "api_key": "sk-secret",
                            "arguments": {"query": "market"},
                        },
                    ),
                    AgentRuntimeEvent(
                        event_type="agent.handoff",
                        message="Handed to analyst",
                        payload={
                            "source_agent": "Researcher",
                            "target_agent": "Analyst",
                            "token": "hidden-token",
                        },
                    ),
                    AgentRuntimeEvent(
                        event_type="tool.call.blocked",
                        message="Tool blocked by policy",
                        payload={
                            "tool_name": "deploy",
                            "reason": "approval_required",
                            "authorization": "Bearer hidden",
                        },
                    ),
                    AgentRuntimeEvent(
                        event_type="run.waiting_runtime",
                        message="Waiting for self-hosted MCP result",
                        payload={"worker_id": "local-1", "external_ref": "secret-ref"},
                    ),
                    AgentRuntimeEvent(
                        event_type="model.fallback.selected",
                        message="Using backup model",
                        payload={"model": "backup", "base_url": "https://secret.example"},
                    ),
                    AgentRuntimeEvent(
                        event_type="debug.trace",
                        message="Noisy internal event",
                        payload={"secret": "still-hidden"},
                    ),
                ),
            )

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="runtime-events",
    )

    WorkerJobHandler(session, agent_runner=EventfulRunner()).handle(job)

    messages = session.scalars(
        select(TaskMessage)
        .where(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.sequence)
    ).all()
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()
    event_types = [event.event_type for event in events]

    runtime_messages = [
        message
        for message in messages
        if message.message_type
        in {
            "tool.requested",
            "agent.handoff",
            "tool.blocked",
            "runtime.waiting",
            "model.fallback",
        }
    ]
    assert [message.message_type for message in runtime_messages] == [
        "tool.requested",
        "agent.handoff",
        "tool.blocked",
        "runtime.waiting",
        "model.fallback",
    ]
    assert runtime_messages[0].payload["tool_name"] == "search"
    assert runtime_messages[0].payload["api_key"] == "[redacted]"
    assert runtime_messages[0].payload["work_package_id"] == "research-1"
    assert runtime_messages[1].payload["token"] == "[redacted]"
    assert runtime_messages[2].payload["authorization"] == "[redacted]"
    assert runtime_messages[3].payload["external_ref"] == "[redacted]"
    assert runtime_messages[4].payload["base_url"] == "[redacted]"
    assert "debug.trace" in event_types
    assert "debug.trace" not in {message.message_type for message in messages}
    serialized_events = json.dumps([event.event_metadata for event in events])
    assert "sk-secret" not in serialized_events
    assert "hidden-token" not in serialized_events
    assert "Bearer hidden" not in serialized_events
    assert "secret-ref" not in serialized_events
    assert "https://secret.example" not in serialized_events
    assert "[redacted]" in serialized_events


def test_worker_persists_structured_task_progress_from_agent_output() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        domain_type="novel",
        title="Draft chapter",
        status=TaskStatus.QUEUED.value,
        input={"outline": {"acts": 2}},
        generic_state={"word_count": 1000, "nested": {"kept": True}},
        domain_state={"chapters": [{"title": "Chapter 1", "status": "draft"}]},
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
    session.add_all([task, agent])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=agent.id,
        title="Write chapter",
        work_package_id="chapter-1",
        status="queued",
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

    class ProgressRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            return AgentRunResult(
                final_output=json.dumps(
                    {
                        "summary": "Chapter draft expanded.",
                        "progress": 0.5,
                        "generic_state": {
                            "word_count": 2400,
                            "nested": {"added": "yes"},
                        },
                        "domain_state": {
                            "chapters": [{"title": "Chapter 1", "status": "revised"}],
                            "continuity_notes": ["Keep the clue visible."],
                        },
                        "task_input": {"outline": {"acts": 3}},
                    }
                )
            )

    WorkerJobHandler(session, agent_runner=ProgressRunner()).handle(
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="structured-progress",
        )
    )

    session.refresh(task)
    progress_message = session.scalar(
        select(TaskMessage).where(
            TaskMessage.task_id == task.id,
            TaskMessage.message_type == "task.progress.updated",
        )
    )

    assert task.generic_state == {
        "word_count": 2400,
        "nested": {"kept": True, "added": "yes"},
    }
    assert task.domain_state["chapters"] == [{"title": "Chapter 1", "status": "revised"}]
    assert task.domain_state["continuity_notes"] == ["Keep the clue visible."]
    assert task.input == {"outline": {"acts": 3}}
    assert progress_message is not None
    assert progress_message.payload["changed_fields"] == [
        "generic_state",
        "domain_state",
        "input",
    ]
    assert progress_message.payload["progress"] == 0.5
    assert progress_message.payload["summary"] == "Chapter draft expanded."
    assert step.status == "completed"


def test_agent_request_includes_profile_tool_policy_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        model_settings={"model_api": "chat-completions"},
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
    assert request.model_api == "chat_completions"
    provider_credential_id = request.context.metadata["model_provider_credential_id"]
    assert isinstance(provider_credential_id, str)
    assert request.context.metadata | {
        "persistent_session_key": None,
        "persistent_session_mode": None,
    } == {
        "agent_profile_id": str(agent.id),
            "agent_role": "designer",
            "run_model": agent.model,
            "model_provider_provider": "openai",
            "model_provider_credential_id": provider_credential_id,
            "model_provider_model_api": "chat_completions",
        "authorization_scope": "workspace",
        "authorized_workspace_id": str(workspace.id),
        "authorized_task_id": str(task.id),
        "tool_policy_source": "agent_profile",
        "authorization_snapshot_version": None,
        "agent_mailbox": {
            "scope": {"task_id": str(task.id)},
            "thread_count": 0,
            "message_count": 0,
            "unread_count": 0,
            "pending_count": 0,
            "latest_unread_messages": [],
        },
        "persistent_session_key": None,
        "persistent_session_mode": None,
    }
    assert request.context.metadata["persistent_session_mode"] == "sdk_session"
    assert isinstance(request.context.metadata["persistent_session_key"], str)


def test_agent_request_includes_unread_mailbox_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Inbox task",
        status=TaskStatus.QUEUED.value,
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([task, sender, recipient])
    session.flush()
    thread = AgentMessageThread(workspace_id=workspace.id, task_id=task.id, subject="Handoff")
    session.add(thread)
    session.flush()
    message = AgentMessage(
        workspace_id=workspace.id,
        task_id=task.id,
        thread_id=thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Please continue with token hidden-mailbox-token and key sk-mailbox-secret.",
        payload={"token": "hidden-mailbox-token"},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=recipient.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add_all([message, run])
    session.commit()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="mailbox-context",
    )

    request = RunOrchestrationService(session)._build_agent_request(run, job)

    mailbox = request.context.metadata["agent_mailbox"]
    assert mailbox["thread_count"] == 1
    assert mailbox["message_count"] == 1
    assert mailbox["unread_count"] == 1
    assert mailbox["scope"] == {"task_id": str(task.id)}
    assert mailbox["latest_unread_messages"] == [
        {
            "id": str(message.id),
            "thread_id": str(thread.id),
            "task_id": str(task.id),
            "agent_team_id": None,
            "sender_agent_profile_id": str(sender.id),
            "message_type": "handoff",
            "status": "sent",
            "created_at": message.created_at.isoformat(),
            "body_preview": "[redacted]",
        }
    ]
    assert "hidden-mailbox-token" not in str(mailbox)
    assert "sk-mailbox-secret" not in str(mailbox)


def test_agent_request_mailbox_context_is_scoped_to_current_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    current_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Current inbox task",
        status=TaskStatus.QUEUED.value,
    )
    other_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Other inbox task",
        status=TaskStatus.QUEUED.value,
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([current_task, other_task, sender, recipient])
    session.flush()
    current_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=current_task.id,
        subject="Current handoff",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=other_task.id,
        subject="Other handoff",
    )
    session.add_all([current_thread, other_thread])
    session.flush()
    current_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=current_task.id,
        thread_id=current_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Use the current task handoff.",
    )
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=other_task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="This belongs to another task.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        agent_profile_id=recipient.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add_all([current_message, other_message, run])
    session.commit()

    request = RunOrchestrationService(session)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="task-scoped-mailbox-context",
        ),
    )

    mailbox = request.context.metadata["agent_mailbox"]
    assert mailbox["thread_count"] == 1
    assert mailbox["message_count"] == 1
    assert mailbox["unread_count"] == 1
    assert mailbox["scope"] == {"task_id": str(current_task.id)}
    assert mailbox["latest_unread_messages"][0]["id"] == str(current_message.id)
    assert str(other_message.id) not in str(mailbox)
    assert "another task" not in str(mailbox)


def test_team_agent_mailbox_context_is_scoped_to_runtime_thread() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Runtime Team",
        team_type="software",
        manager_agent_profile_id=sender.id,
    )
    session.add_all([sender, recipient, team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=sender.id,
                team_role="Planner",
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=recipient.id,
                team_role="Builder",
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Team runtime task",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    runtime_state = TeamRuntimeService(session).get_state(
        workspace_id=workspace.id,
        team_id=team.id,
        initialize=True,
    )
    assert runtime_state is not None
    assert runtime_state.thread_id is not None
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=task.id,
        subject="Task-local handoff",
    )
    session.add(other_thread)
    session.flush()
    runtime_message = AgentMessage(
        workspace_id=workspace.id,
        thread_id=runtime_state.thread_id,
        agent_team_id=team.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Use the shared runtime handoff.",
    )
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="This task-local handoff should not enter runtime inbox.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=recipient.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add_all([runtime_message, other_message, run])
    session.commit()

    request = RunOrchestrationService(session)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="team-runtime-scoped-mailbox-context",
        ),
    )

    mailbox = request.context.metadata["agent_mailbox"]
    assert mailbox["thread_count"] == 1
    assert mailbox["message_count"] == 1
    assert mailbox["unread_count"] == 1
    assert mailbox["scope"] == {"thread_id": str(runtime_state.thread_id)}
    assert mailbox["latest_unread_messages"][0]["id"] == str(runtime_message.id)
    assert mailbox["latest_unread_messages"][0]["agent_team_id"] == str(team.id)
    assert str(other_message.id) not in str(mailbox)
    assert "task-local handoff" not in str(mailbox)


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
    expected_metadata = {
        "agent_profile_id": str(agent.id),
        "agent_role": "designer",
        "run_model": agent.model,
        "model_provider_provider": "openai",
        "model_provider_credential_id": request.context.metadata["model_provider_credential_id"],
        "model_provider_model_api": None,
        "authorization_scope": "workspace",
        "authorized_workspace_id": str(workspace.id),
        "authorized_task_id": str(task.id),
        "tool_policy_source": "agent_profile",
        "authorization_snapshot_version": None,
        "context_scope": "task_step",
        "task_step_id": str(step.id),
        "work_package_id": "visual-design",
        "required_role": "designer",
        "required_skills": ["brand_design", "deck_layout"],
        "expected_artifacts": ["pitch_deck"],
        "acceptance_criteria": ["Deck follows the brand system."],
        "review_policy": {"reviewer": "manager", "mode": "manager_review"},
    }
    assert request.context.metadata | expected_metadata == request.context.metadata
    assert request.context.metadata["agent_mailbox"]["unread_count"] == 0
    assert request.context.metadata["persistent_session_mode"] == "sdk_session"
    assert request.tracing is not None
    assert request.tracing.group_id == request.context.metadata["persistent_session_key"]
    assert request.tracing.metadata["task_step_id"] == str(step.id)


def test_agent_request_allows_snapshot_to_narrow_agent_tools() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        tool_policy={"allowed_tools": ["generate_image", "write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "version": 1,
                "workspace_id": str(workspace.id),
                "task_id": str(task.id),
                "agent_profile_id": str(agent.id),
                "allowed_tools": ["write_artifact"],
            }
        },
    )
    session.add(run)
    session.commit()

    request = RunOrchestrationService(session)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="narrow-tools",
        ),
    )

    assert request.context.allowed_tools == ("write_artifact",)


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
        provider="anthropic",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        is_default=True,
        budget_metadata={"model_api": "anthropic_messages"},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Custom",
        role="writer",
        instructions="Write.",
        model="workspace-default",
        model_provider_credential_id=credential.id,
        model_settings={"model_api": "response"},
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

    assert request.model == "claude-sonnet-4-5"
    assert request.provider == "anthropic"
    assert request.base_url == "https://api.anthropic.com"
    assert request.api_key == "anthropic-key"
    assert request.model_api == "anthropic_messages"
    assert request.model_provider_credential_id == credential.id
    assert request.context.metadata["run_model"] == "claude-sonnet-4-5"
    assert request.context.metadata["model_provider_provider"] == "anthropic"
    assert request.context.metadata["model_provider_credential_id"] == str(credential.id)
    assert request.context.metadata["model_provider_model_api"] == "anthropic_messages"
    assert request.tracing is not None
    assert request.tracing.metadata["run_model"] == "claude-sonnet-4-5"
    assert request.tracing.metadata["model_provider_provider"] == "anthropic"
    assert request.tracing.metadata["model_provider_credential_id"] == str(credential.id)
    assert request.tracing.metadata["model_provider_model_api"] == "anthropic_messages"
    serialized_tracing = json.dumps(request.tracing.metadata)
    assert "api_key" not in serialized_tracing
    assert "base_url" not in serialized_tracing


def test_agent_request_model_api_overrides_credential_default_protocol() -> None:
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
        name="Router",
        provider="openai-compatible",
        api_key="router-key",
        default_model="router/default",
        base_url="https://router.example.test/v1",
        is_default=True,
        budget_metadata={"model_api": "chat-completions"},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Custom",
        role="writer",
        instructions="Write.",
        model="workspace-default",
        model_provider_credential_id=credential.id,
        model_settings={"model_api": "response"},
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
            idempotency_key="agent-model-api-override",
        ),
    )

    assert request.model == "router/default"
    assert request.model_api == "responses"
    assert request.context.metadata["model_provider_model_api"] == "responses"
    assert request.tracing is not None
    assert request.tracing.metadata["model_provider_model_api"] == "responses"


def test_agent_request_re_resolves_workspace_default_snapshot_when_provider_unhealthy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary Default",
        provider="openai-compatible",
        api_key="sk-primary",
        default_model="primary-default",
        base_url="https://primary.example.test/v1",
        is_default=True,
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai-compatible",
        api_key="sk-backup",
        default_model="backup-default",
        base_url="https://backup.example.test/v1",
        is_default=False,
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Defaulted",
        role="writer",
        instructions="Write.",
        model="workspace-default",
    )
    session.add_all([task, agent])
    session.flush()
    snapshot = RunOrchestrationService(
        session,
        settings=settings,
    )._model_provider_snapshot(workspace.id, agent)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": {"model_provider": snapshot}},
    )
    session.add(run)
    session.commit()

    primary.health_status = "unhealthy"
    session.commit()

    request = RunOrchestrationService(session, settings=settings)._build_agent_request(
        run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=run.id,
            requested_by_user_id=user.id,
            idempotency_key="provider-snapshot-reresolve",
        ),
    )

    assert snapshot["source"] == "workspace_default"
    assert snapshot["credential_id"] == str(primary.id)
    assert request.model == "backup-default"
    assert request.api_key == "sk-backup"
    assert request.base_url == "https://backup.example.test/v1"
    assert request.model_provider_credential_id == backup.id


def test_agent_request_does_not_fallback_explicit_inactive_provider_override() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    inactive = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Explicit Inactive",
        provider="openai-compatible",
        api_key="sk-inactive-explicit",
        default_model="inactive-model",
        base_url="https://inactive.example.test/v1",
        is_default=False,
    )
    inactive.status = "inactive"
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Workspace Backup",
        provider="openai-compatible",
        api_key="sk-backup",
        default_model="backup-model",
        base_url="https://backup.example.test/v1",
        is_default=True,
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Explicit",
        role="writer",
        instructions="Write.",
        model="workspace-default",
        model_provider_credential_id=inactive.id,
    )
    session.add_all([task, agent])
    session.flush()
    snapshot = {
        "source": "agent_override",
        "selected_model": "inactive-model",
        "agent_model": "workspace-default",
        "credential_id": str(inactive.id),
        "credential_reference": f"model_provider_credentials:{inactive.id}",
        "credential_name": "Explicit Inactive",
        "provider": "openai-compatible",
        "default_model": "inactive-model",
        "base_url_host": "inactive.example.test",
        "base_url_configured": True,
        "api_key_fingerprint": inactive.api_key_fingerprint,
        "is_default": False,
        "model_api": None,
    }
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": {"model_provider": snapshot}},
    )
    session.add(run)
    session.commit()

    with pytest.raises(ValueError, match="not found or unavailable"):
        RunOrchestrationService(session, settings=settings)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="explicit-inactive-provider",
            ),
        )

    assert snapshot["source"] == "agent_override"
    assert snapshot["credential_id"] == str(inactive.id)
    assert backup.status == "active"
    assert "sk-inactive-explicit" not in str(snapshot)


def test_worker_fails_closed_without_model_provider_fallback() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai-compatible",
        api_key="sk-primary",
        default_model="primary-model",
        base_url="https://primary.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai-compatible",
        api_key="sk-backup",
        default_model="backup-default",
        base_url="https://backup.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "responses"},
    )
    workspace.settings = {
        "model_provider_fallback": {
            "enabled": True,
            "retry_error_codes": ["RuntimeError"],
            "candidates": [{"credential_id": str(backup.id), "model": "backup-model"}],
        }
    }
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write.",
        model="primary-model",
        model_provider_credential_id=primary.id,
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Fallback Team",
        team_type="software",
        manager_agent_profile_id=agent.id,
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="developer",
            order_index=1,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
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

    class FailingRunner:
        def __init__(self) -> None:
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            raise RuntimeError(
                "primary provider unavailable api_key=sk-fallback-secret "
                "Bearer fallback-token "
                "base_url=https://primary.example.test/v1/private"
            )

    runner = FailingRunner()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="provider-fallback",
    )

    with pytest.raises(RuntimeError):
        RunOrchestrationService(session, agent_runner=runner, settings=settings).run_agent_sync(job)

    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()
    event_types = {event.event_type for event in events}
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "model_provider.used",
            AuditEvent.target_id == str(run.id),
        )
    )
    team_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == team.id,
            AgentMessage.message_type == "team.runtime.model_provider.fallback_selected",
        )
    )

    assert [request.model for request in runner.requests] == ["primary-model"]
    assert runner.requests[0].api_key == "sk-primary"
    assert [request.model_api for request in runner.requests] == ["chat_completions"]
    assert primary.health_status == "degraded"
    assert primary.last_failure_code == "RuntimeError"
    assert primary.last_failure_message == "[redacted]"
    assert primary.last_failure_at is not None
    assert backup.health_status == "unknown"
    assert backup.last_success_at is None
    assert run.status == RunStatus.FAILED.value
    assert "model_provider.fallback_selected" not in event_types
    assert team_message is None
    assert audit is None


def test_worker_falls_back_across_model_provider_vendors() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary OpenAI Gateway",
        provider="openai-compatible",
        api_key="sk-primary",
        default_model="primary-model",
        base_url="https://primary.example.test/v1",
        is_default=False,
        budget_metadata={"model_api": "chat_completions"},
    )
    anthropic_backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Claude Backup",
        provider="anthropic",
        api_key="anthropic-key",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com",
        is_default=False,
    )
    workspace.settings = {
        "model_provider_fallback": {
            "enabled": True,
            "retry_error_codes": ["RuntimeError"],
            "candidates": [
                {
                    "credential_id": str(anthropic_backup.id),
                    "model": "claude-sonnet-4-5",
                }
            ],
        }
    }
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write.",
        model="primary-model",
        model_provider_credential_id=primary.id,
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Unavailable Fallback Team",
        team_type="software",
        manager_agent_profile_id=agent.id,
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="developer",
            order_index=1,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
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

    class FallbackRunner:
        def __init__(self) -> None:
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            if len(self.requests) == 1:
                raise RuntimeError("primary provider unavailable")
            return AgentRunResult(final_output=f"handled by {request.provider}")

    runner = FallbackRunner()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="cross-provider-fallback",
    )

    RunOrchestrationService(session, agent_runner=runner, settings=settings).run_agent_sync(job)

    fallback_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "model_provider.fallback_selected",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "model_provider.used",
            AuditEvent.target_id == str(run.id),
        )
    )

    assert [(request.provider, request.model) for request in runner.requests] == [
        ("openai-compatible", "primary-model"),
        ("anthropic", "claude-sonnet-4-5"),
    ]
    assert runner.requests[1].api_key == "anthropic-key"
    assert runner.requests[1].base_url == "https://api.anthropic.com"
    assert runner.requests[1].model_api == "anthropic_messages"
    assert fallback_event is not None
    assert fallback_event.event_metadata["failed_provider"] == {
        "provider": "openai-compatible",
        "model": "primary-model",
        "model_api": "chat_completions",
        "credential_id": str(primary.id),
    }
    selected = fallback_event.event_metadata["model_provider"]
    assert selected["provider"] == "anthropic"
    assert selected["credential_id"] == str(anthropic_backup.id)
    assert selected["selected_model"] == "claude-sonnet-4-5"
    assert selected["model_api"] == "anthropic_messages"
    assert "api_key" not in selected
    assert "base_url" not in selected
    assert audit is not None
    assert audit.audit_metadata["provider"] == "anthropic"
    assert audit.audit_metadata["model"] == "claude-sonnet-4-5"
    assert audit.audit_metadata["model_api"] == "anthropic_messages"
    assert audit.audit_metadata["credential_id"] == str(anthropic_backup.id)


def test_worker_ignores_budget_exhausted_model_provider_fallback_policy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    )
    primary = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai-compatible",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=False,
    )
    exhausted = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Exhausted",
        provider="openai-compatible",
        api_key="sk-exhausted",
        default_model="exhausted-model",
        base_url=None,
        is_default=False,
        budget_metadata={"limits": {"calls": 1}, "usage": {"calls": 1}},
    )
    backup = service.create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Backup",
        provider="openai-compatible",
        api_key="sk-backup",
        default_model="backup-model",
        base_url=None,
        is_default=False,
    )
    workspace.settings = {
        "model_provider_fallback": {
            "enabled": True,
            "retry_error_codes": ["RuntimeError"],
            "candidates": [
                {"credential_id": str(exhausted.id), "model": "exhausted-model"},
                {"credential_id": str(backup.id), "model": "backup-model"},
            ],
        }
    }
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write.",
        model="primary-model",
        model_provider_credential_id=primary.id,
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Unavailable Fallback Team",
        team_type="software",
        manager_agent_profile_id=agent.id,
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="developer",
            order_index=1,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
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

    class FailingRunner:
        def __init__(self) -> None:
            self.requests: list[AgentRunRequest] = []

        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            self.requests.append(request)
            raise RuntimeError("primary provider unavailable")

    runner = FailingRunner()
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="provider-budget-fallback",
    )

    with pytest.raises(RuntimeError):
        RunOrchestrationService(session, agent_runner=runner, settings=settings).run_agent_sync(job)

    fallback_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "model_provider.fallback_selected",
        )
    )
    assert [request.model for request in runner.requests] == ["primary-model"]
    assert all(request.model != "exhausted-model" for request in runner.requests)
    assert all(request.model != "backup-model" for request in runner.requests)
    assert fallback_event is None
    assert exhausted.status == "active"
    assert backup.status == "active"
    assert run.status == RunStatus.FAILED.value


def test_worker_rejects_cross_workspace_model_provider_fallback() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    other_user = User(email="other@example.com", display_name="Other")
    other_workspace = Workspace(owner=other_user, name="Other", slug="other", settings={})
    session.add_all(
        [
            other_user,
            other_workspace,
            WorkspaceMember(workspace=other_workspace, user=other_user, role="owner"),
        ]
    )
    session.commit()
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    secret_service = SecretEncryptionService(
        secret=settings.credential_encryption_secret,
        key_id=settings.credential_encryption_key_id,
    )
    primary = ModelProviderCredentialService(session, secret_service).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Primary",
        provider="openai",
        api_key="sk-primary",
        default_model="primary-model",
        base_url=None,
        is_default=False,
    )
    foreign = ModelProviderCredentialService(session, secret_service).create(
        workspace_id=other_workspace.id,
        created_by_user_id=other_user.id,
        name="Foreign",
        provider="openai",
        api_key="sk-foreign",
        default_model="foreign-model",
        base_url=None,
        is_default=False,
    )
    workspace.settings = {
        "model_provider_fallback": {
            "enabled": True,
            "candidates": [{"credential_id": str(foreign.id), "model": "foreign-model"}],
        }
    }
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write.",
        model="primary-model",
        model_provider_credential_id=primary.id,
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Unavailable Fallback Team",
        team_type="software",
        manager_agent_profile_id=agent.id,
        default_task_policy={"team_runtime": {"status": "running"}},
    )
    session.add_all([agent, team])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="developer",
            order_index=1,
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
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

    class FailingRunner:
        async def run(self, request: AgentRunRequest) -> AgentRunResult:
            raise RuntimeError(
                "primary provider unavailable api_key=sk-unavailable-secret "
                "Bearer unavailable-token "
                "base_url=https://primary.example.test/v1/private"
            )

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key="blocked-provider-fallback",
    )

    try:
        RunOrchestrationService(
            session,
            agent_runner=FailingRunner(),
            settings=settings,
        ).run_agent_sync(job)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected run failure when fallback crosses workspace")

    event_types = [
        event.event_type
        for event in session.scalars(
            select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
        ).all()
    ]
    fallback_unavailable_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "model_provider.fallback_unavailable",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "model_provider.fallback_unavailable",
            AuditEvent.target_id == str(run.id),
        )
    )
    team_message = session.scalar(
        select(AgentMessage).where(
            AgentMessage.workspace_id == workspace.id,
            AgentMessage.agent_team_id == team.id,
            AgentMessage.message_type == "team.runtime.model_provider.fallback_unavailable",
        )
    )
    assert "model_provider.fallback_selected" not in event_types
    assert "model_provider.fallback_unavailable" not in event_types
    assert fallback_unavailable_event is None
    assert team_message is None
    assert audit is None
    assert foreign.status == "active"
    assert run.status == RunStatus.FAILED.value
    assert run.error["message"] == "[redacted]"


def test_agent_request_rejects_foreign_workspace_agent_profile() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    foreign_user = User(email="foreign@example.com", display_name="Foreign")
    other_workspace = Workspace(owner=foreign_user, name="Other", slug="other", settings={})
    foreign_membership = WorkspaceMember(
        workspace=other_workspace,
        user=foreign_user,
        role="owner",
    )
    session.add_all([foreign_user, other_workspace, foreign_membership])
    session.flush()
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    foreign_agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Foreign",
        role="writer",
    )
    session.add_all([task, foreign_agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=foreign_agent.id,
        status=RunStatus.QUEUED.value,
        input={},
    )
    session.add(run)
    session.commit()

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="foreign-agent-context",
            ),
        )
    except ValueError as exc:
        assert "agent profile workspace mismatch" in str(exc)
    else:
        raise AssertionError("Expected foreign agent profile to be rejected")


def test_agent_request_rejects_task_step_from_another_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    other_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Other task",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
    session.add_all([task, other_task, agent])
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=other_task.id,
        assigned_agent_profile_id=agent.id,
        title="Other step",
        status="queued",
        order_index=1,
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

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="foreign-step-context",
            ),
        )
    except ValueError as exc:
        assert "task step does not belong" in str(exc)
    else:
        raise AssertionError("Expected foreign task step to be rejected")


def test_agent_request_rejects_worker_job_scope_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
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

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=uuid4(),
                requested_by_user_id=user.id,
                idempotency_key="wrong-resource",
            ),
        )
    except ValueError as exc:
        assert "resource does not match" in str(exc)
    else:
        raise AssertionError("Expected worker job resource mismatch to be rejected")


def test_agent_request_rejects_authorization_snapshot_scope_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(uuid4()),
                "task_id": str(task.id),
                "agent_profile_id": str(agent.id),
            }
        },
    )
    session.add(run)
    session.commit()

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="bad-snapshot-scope",
            ),
        )
    except ValueError as exc:
        assert "Authorization snapshot workspace_id mismatch" in str(exc)
    else:
        raise AssertionError("Expected authorization snapshot mismatch to be rejected")


def test_agent_request_rejects_authorization_snapshot_tool_escalation() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        tool_policy={"allowed_tools": ["write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "task_id": str(task.id),
                "agent_profile_id": str(agent.id),
                "allowed_tools": ["write_artifact", "delete_workspace_file"],
            }
        },
    )
    session.add(run)
    session.commit()

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="bad-tool-snapshot",
            ),
        )
    except ValueError as exc:
        assert "outside agent policy" in str(exc)
    else:
        raise AssertionError("Expected tool escalation snapshot to be rejected")


def test_agent_request_rejects_authorization_snapshot_unavailable_skill() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        skills={"installed_skill_ids": []},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "task_id": str(task.id),
                "agent_profile_id": str(agent.id),
                "installed_skills": [{"install_id": str(uuid4())}],
            }
        },
    )
    session.add(run)
    session.commit()

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="bad-skill-snapshot",
            ),
        )
    except ValueError as exc:
        assert "unavailable workspace skill" in str(exc)
    else:
        raise AssertionError("Expected unavailable skill snapshot to be rejected")


def test_agent_request_rejects_authorization_snapshot_skill_provenance_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        skills={},
    )
    skill = Skill(
        key="writer-pro",
        name="Writer Pro",
        version="1.0.0",
        capability_keys=["writing"],
        manifest={"prompt": "v1"},
        visibility="public",
    )
    session.add_all([task, agent, skill])
    session.flush()
    install = WorkspaceSkillInstall(
        workspace_id=workspace.id,
        skill_id=skill.id,
        installed_by_user_id=user.id,
        installed_key=skill.key,
        installed_name=skill.name,
        installed_version=skill.version,
        installed_description=skill.description,
        installed_capability_keys=skill.capability_keys,
        installed_manifest=skill.manifest,
        source_visibility=skill.visibility,
        source_checksum="sha256:v1",
    )
    session.add(install)
    session.flush()
    agent.skills = {"installed_skill_ids": [str(install.id)]}
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "task_id": str(task.id),
                "agent_profile_id": str(agent.id),
                "installed_skills": [
                    {
                        "install_id": str(install.id),
                        "source_skill_id": str(skill.id),
                        "installed_key": "writer-pro",
                        "installed_name": "Writer Pro",
                        "installed_version": "1.0.0",
                        "installed_capability_keys": ["writing"],
                        "source_checksum": "sha256:tampered",
                        "source_visibility": "public",
                    }
                ],
            }
        },
    )
    session.add(run)
    session.commit()

    try:
        RunOrchestrationService(session)._build_agent_request(
            run,
            JobPayload(
                workspace_id=workspace.id,
                job_type=JobType.AGENT_RUN,
                resource_id=run.id,
                requested_by_user_id=user.id,
                idempotency_key="bad-skill-provenance",
            ),
        )
    except ValueError as exc:
        assert "skill provenance mismatch" in str(exc)
    else:
        raise AssertionError("Expected skill provenance mismatch to be rejected")


def test_team_agent_runs_share_persistent_sdk_session_across_tasks() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team Runtime Space",
        status="active",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Researcher",
        role="researcher",
        instructions="Keep long-running company context.",
        model="researcher-model",
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Research Team",
        team_type="research",
        runtime_space_id=runtime_space.id,
    )
    session.add_all([runtime_space, agent, team])
    session.flush()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        name="Research runtime",
        status="running",
        connection_status="online",
    )
    session.add(runtime)
    session.flush()
    team.default_task_policy = {
        "team_runtime": {
            "status": "running",
            "workspace_runtime_id": str(runtime.id),
            "last_heartbeat_at": datetime.now(UTC).isoformat(),
        }
    }
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="Research",
        )
    )
    first_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="First market question",
    )
    second_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Second market question",
    )
    session.add_all([first_task, second_task])
    session.flush()
    first_step = TaskStep(
        workspace_id=workspace.id,
        task_id=first_task.id,
        assigned_agent_profile_id=agent.id,
        title="Research first",
        status="queued",
    )
    second_step = TaskStep(
        workspace_id=workspace.id,
        task_id=second_task.id,
        assigned_agent_profile_id=agent.id,
        title="Research second",
        status="queued",
    )
    session.add_all([first_step, second_step])
    session.flush()
    orchestration = RunOrchestrationService(session)
    first_run = orchestration._create_run_for_step(first_task, first_step)
    second_run = orchestration._create_run_for_step(second_task, second_step)

    first_request = RunOrchestrationService(session)._build_agent_request(
        first_run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=first_run.id,
            requested_by_user_id=user.id,
            idempotency_key="first-persistent-session",
        ),
    )
    second_request = RunOrchestrationService(session)._build_agent_request(
        second_run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=second_run.id,
            requested_by_user_id=user.id,
            idempotency_key="second-persistent-session",
        ),
    )

    assert first_request.session is not None
    assert second_request.session is not None
    assert first_request.session.session_id == second_request.session.session_id
    assert first_request.session.session_id == f"{workspace.id}:team_agent:{team.id}:{agent.id}"
    assert first_request.context.metadata["persistent_session_mode"] == "sdk_session"
    assert first_request.context.metadata["team_context"]["team_id"] == str(team.id)
    assert first_request.context.metadata["team_context"]["current_member"]["team_role"] == (
        "Research"
    )
    assert first_request.context.metadata["team_context"]["runtime"]["status"] == "running"
    assert first_request.context.metadata["team_context"]["runtime"]["workspace_runtime_id"] == (
        str(runtime.id)
    )
    assert first_request.context.metadata["team_context"]["runtime"]["runtime_space_id"] == (
        str(runtime_space.id)
    )
    assert first_request.tracing is not None
    assert first_request.tracing.workflow_name == "chaincloud.team_agent_run"
    assert first_request.tracing.group_id == first_request.session.session_id
    assert first_request.tracing.metadata["team"]["team_id"] == str(team.id)
    assert first_request.tracing.metadata["team"]["current_member"]["team_role"] == "Research"
    assert first_request.tracing.metadata["team"]["runtime"] == {
        "status": "running",
        "workspace_runtime_id": str(runtime.id),
        "runtime_status": "running",
        "runtime_space_id": str(runtime_space.id),
        "thread_id": first_request.context.metadata["team_context"]["runtime"]["thread_id"],
        "team_session_id": first_request.context.metadata["team_context"]["runtime"][
            "team_session_id"
        ],
        "member_session_count": 1,
    }
    assert first_request.tracing.metadata["persistent_session_key"] == (
        first_request.session.session_id
    )
    assert "Team context:" in first_request.input_text
    assert "Research Team" in first_request.input_text


def test_team_agents_exchange_mailbox_across_persistent_runs() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    planner = AgentProfile(
        workspace_id=workspace.id,
        name="Planner",
        role="planner",
        tool_policy={
            "allowed_tools": [
                "send_agent_message",
                "get_agent_inbox",
                "mark_agent_message_read",
            ]
        },
    )
    builder = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="builder",
        tool_policy={
            "allowed_tools": [
                "send_agent_message",
                "get_agent_inbox",
                "mark_agent_message_read",
            ]
        },
    )
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Product Team",
        team_type="software",
        manager_agent_profile_id=planner.id,
    )
    session.add_all([planner, builder, team])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=planner.id,
                team_role="Planner",
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=builder.id,
                team_role="Builder",
                order_index=2,
            ),
        ]
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Build mailbox flow",
    )
    session.add(task)
    session.flush()
    planner_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=planner.id,
        title="Plan handoff",
        status="queued",
        order_index=1,
    )
    builder_step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        assigned_agent_profile_id=builder.id,
        title="Build handoff",
        status="queued",
        order_index=2,
    )
    session.add_all([planner_step, builder_step])
    session.flush()
    orchestration = RunOrchestrationService(session)
    planner_run = orchestration._create_run_for_step(task, planner_step)
    builder_run = orchestration._create_run_for_step(task, builder_step)
    session.commit()

    planner_request = RunOrchestrationService(session)._build_agent_request(
        planner_run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=planner_run.id,
            requested_by_user_id=user.id,
            idempotency_key="planner-mailbox-context",
        ),
    )
    assert planner_request.context.allowed_tools == (
        "send_agent_message",
        "get_agent_inbox",
        "mark_agent_message_read",
    )
    assert planner_request.session is not None
    send_result = planner_request.tool_executor.execute_tool(
        context=planner_request.context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(builder.id),
            "subject": "Builder handoff",
            "body": "Start with the runtime ensure path.",
        },
    )
    assert send_result.status == "completed"
    assert send_result.output is not None
    assert send_result.output["thread"]["id"] == (
        planner_request.context.metadata["agent_mailbox"]["scope"]["thread_id"]
    )
    assert send_result.output["thread"]["task_id"] is None
    assert send_result.output["thread"]["agent_team_id"] == str(team.id)
    assert send_result.output["message"]["task_id"] is None
    assert send_result.output["message"]["agent_team_id"] == str(team.id)

    builder_request = RunOrchestrationService(session)._build_agent_request(
        builder_run,
        JobPayload(
            workspace_id=workspace.id,
            job_type=JobType.AGENT_RUN,
            resource_id=builder_run.id,
            requested_by_user_id=user.id,
            idempotency_key="builder-mailbox-context",
        ),
    )
    assert builder_request.session is not None
    assert builder_request.session.session_id == f"{workspace.id}:team_agent:{team.id}:{builder.id}"
    assert builder_request.context.metadata["agent_mailbox"]["unread_count"] == 1
    assert builder_request.context.metadata["agent_mailbox"]["latest_unread_messages"][0][
        "body_preview"
    ] == "Start with the runtime ensure path."
    assert builder_request.context.metadata["team_context"]["team_name"] == "Product Team"
    assert "Team context:" in builder_request.input_text


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


def _seed_workspace(
    session: Session,
    *,
    with_default_provider: bool = True,
) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    if with_default_provider:
        _seed_default_model_provider(
            session,
            workspace_id=workspace.id,
            user_id=user.id,
        )
    return user, workspace


def _seed_default_model_provider(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    api_key: str = "sk-unit-test-provider",
) -> None:
    ModelProviderCredentialService(
        session,
        SecretEncryptionService(secret="change-me-credential-encryption-secret", key_id="local"),
    ).create(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name="Unit test provider",
        provider="openai",
        api_key=api_key,
        default_model="gpt-4.1",
        base_url=None,
        is_default=True,
    )


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
