import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.agents.execution.contracts import (
    AgentRunResult,
    AgentStructuredOutput,
)
from opsmesh.agents.execution.tools.executor import AgentToolExecutionService
from opsmesh.agents.messages.models import AgentMessage, AgentMessageThread
from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.capabilities.mcp.models import MCPCredentialReference, MCPServer, MCPToolAllowlist
from opsmesh.capabilities.references.models import CapabilityResource
from opsmesh.capabilities.skills.models import Skill, WorkspaceSkillInstall
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.planning.attempt_models import TaskPlanningAttempt
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.activity_summary import activity_phase
from opsmesh.orchestration.runs.authorization.snapshot import RunAuthorizationSnapshotService
from opsmesh.orchestration.runs.models import (
    AUTHORIZATION_SNAPSHOT_VERSION,
    AgentRun,
    RunEvent,
)
from opsmesh.orchestration.runs.resources import (
    release_run_reservations,
)
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.tasks.models import Task, TaskMessage, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.resources.memory.models import (
    WorkspaceMemoryEntry,
    WorkspaceMemoryRetrievalEvent,
)
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.teams.sessions.service import TeamOperationService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.fixtures.execution import author_fixture_plan, test_agent_id
from tests.fixtures.run_product import (
    _authorization_snapshot,
    _authorized_run_input,
    _build_agent_request,
    _run_lifecycle,
    _run_step_launcher,
    _seed_summary_ready_task,
    _semantic_memory_entry,
)
from tests.fixtures.tools import execute_tool


def test_run_activity_maps_precise_execution_lifecycle_events() -> None:
    assert activity_phase("running", "run.claimed") == "worker_claimed"
    assert activity_phase("running", "run.context_built") == "context_ready"
    assert activity_phase("running", "model.request_started") == "model_running"
    assert activity_phase("running", "model.response_received") == "model_processing"
    assert activity_phase("running", "model.request_failed") == "model_failed"


def test_completed_run_writes_episodic_memory_by_default() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer token=agent-name-secret",
        role="writer",
        instructions="Write clearly.",
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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

    _run_lifecycle(session).mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete with token=memory-secret."),
        requested_by_user_id=user.id,
    )

    entry = session.scalar(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.source_type == "agent_run",
            WorkspaceMemoryEntry.source_id == str(run.id),
            WorkspaceMemoryEntry.entry_type == "agent_run_completed",
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


def test_completed_run_episodic_capture_can_be_disabled() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write clearly.",
        memory_policy={"episodic_memory": {"capture_enabled": False}},
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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

    _run_lifecycle(session).mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete."),
        requested_by_user_id=user.id,
    )

    assert session.scalar(select(WorkspaceMemoryEntry)) is None


def test_completed_run_episodic_capture_is_idempotent_for_same_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        instructions="Write clearly.",
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
    lifecycle = _run_lifecycle(session)

    lifecycle.mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete."),
        requested_by_user_id=user.id,
    )
    run.status = RunStatus.RUNNING.value
    session.flush()
    lifecycle.mark_run_completed(
        run,
        AgentRunResult(final_output="Launch note complete again."),
        requested_by_user_id=user.id,
    )

    assert session.scalar(select(func.count(WorkspaceMemoryEntry.id))) == 2


def test_runtime_space_reserves_multi_resource_capacity_for_team_steps() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Build Space",
        scope="team",
        policy={
            "reservation_usage": {"sandbox_sessions": 1, "storage_mb": 256},
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
            ("sandbox_sessions", 2, "count"),
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
    session.add_all(
        [
            RuntimeSpaceBinding(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                target_type="agent_team",
                target_id=team.id,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=developer.id,
                team_role="Developer",
                order_index=0,
            ),
        ]
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
                    "reservation_usage": {"business_operations": 1},
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
                    "reservation_usage": {"business_operations": 1},
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
            "reservation_usage": {"business_operations": 1},
        },
    )
    session.add(build_step)
    session.flush()

    launcher = _run_step_launcher(session)
    run = launcher.create_reserved_run_for_step(task, build_step)

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
        "sandbox_sessions": 1,
        "storage_mb": 512,
        "cpu": 2,
        "memory_mb": 1536,
        "artifact_mb": 50,
        "business_operations": 1,
    }
    quota_by_key = {
        quota.quota_key: quota
        for quota in session.scalars(
            select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.runtime_space_id == runtime_space.id)
        ).all()
    }
    assert quota_by_key["active_runs"].reserved_value == 1
    assert quota_by_key["sandbox_sessions"].reserved_value == 1
    assert quota_by_key["cpu"].reserved_value == 2
    assert quota_by_key["memory_mb"].reserved_value == 1536
    assert quota_by_key["storage_mb"].reserved_value == 512
    assert quota_by_key["artifact_mb"].reserved_value == 50
    assert "business_operations" not in quota_by_key

    release_run_reservations(session, run, released_at=datetime.now(UTC))

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
        scope="workspace",
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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

    run = _run_step_launcher(session).create_reserved_run_for_step(task, step)

    assert run is None
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:memory_mb"
    session.refresh(quota)
    assert quota.reserved_value == 0


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
    lifecycle = _run_lifecycle(session)
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(
        run,
        AgentRunResult(
            final_output=output,
            raw_output=json.loads(output),
            structured_output=AgentStructuredOutput(
                value=json.loads(output), schema_name="pm_acceptance", validated=True
            ),
        ),
        requested_by_user_id=user.id,
    )
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
    lifecycle = _run_lifecycle(session)
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(
        run,
        AgentRunResult(
            final_output=output,
            raw_output=json.loads(output),
            structured_output=AgentStructuredOutput(
                value=json.loads(output), schema_name="pm_acceptance", validated=True
            ),
        ),
        requested_by_user_id=user.id,
    )
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
    lifecycle = _run_lifecycle(session)
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(
        run,
        AgentRunResult(
            final_output=output,
            raw_output=json.loads(output),
            structured_output=AgentStructuredOutput(
                value=json.loads(output), schema_name="pm_acceptance", validated=True
            ),
        ),
        requested_by_user_id=user.id,
    )
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
    lifecycle = _run_lifecycle(session)
    lifecycle.mark_run_started(run)
    lifecycle.mark_run_completed(
        run,
        AgentRunResult(
            final_output=output,
            raw_output=json.loads(output),
            structured_output=AgentStructuredOutput(
                value=json.loads(output), schema_name="pm_acceptance", validated=True
            ),
        ),
        requested_by_user_id=user.id,
    )
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
    assert messages[3].payload["follow_up_work_package_ids"] == ["revision-Research-1-1-1"]


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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
            "network": {"mode": "none"},
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
    server = MCPServer(
        workspace_id=workspace.id,
        name="Image MCP",
        server_type="streamable_http",
        connection={"url": "https://mcp.example.test/rpc"},
        health_status="healthy",
        last_health_check_at=datetime.now(UTC),
    )
    session.add(server)
    session.flush()
    allow = MCPToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        capability_key="image.generate",
        requires_approval=True,
        risk_level="medium",
        policy={"timeout_seconds": 12},
    )
    credential_ref = MCPCredentialReference(
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
    task.input = author_fixture_plan(session, task)
    session.add(task)
    session.flush()

    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    snapshot = run.input["authorization_snapshot"]
    agent.tool_policy = {"allowed_tools": ["delete_workspace_file"]}
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
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
                    "server_type": "streamable_http",
                    "mcp_server_configuration_version": server.configuration_version,
                    "mcp_tool_configuration_version": allow.configuration_version,
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
                            "configuration_version": credential_ref.configuration_version,
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
                    "configuration_version": credential_ref.configuration_version,
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
        "network": {"mode": "none"},
        "mcp": {
            "timeout_seconds": 45,
            "max_input_bytes": 64_000,
            "max_output_bytes": 512_000,
        },
    }
    assert snapshot["approval_policy"] == {"required_tools": ["write_artifact"]}
    assert request.context.allowed_tools == ("generate_image",)
    assert request.tool_executor is None
    assert (
        request.context.metadata["authorization_snapshot_version"] == AUTHORIZATION_SNAPSHOT_VERSION
    )
    assert request.context.metadata["authorization_snapshot_fingerprint"] == snapshot["fingerprint"]
    assert request.context.tool_definitions[0].name == "generate_image"
    assert request.context.tool_definitions[0].mcp_server_id == server.id


def test_queued_team_run_freezes_model_provider_snapshot_without_secret() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="unit-test-secret",
        credential_encryption_key_id="test-key",
    )
    credential = ModelProviderCredentialCommandService(
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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

    task.input = author_fixture_plan(session, task)
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
    assert snapshot["model_capability"] is None
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
                "model_api": "chat_completions",
            },
        },
    }
    credential.budget_metadata = {"model_api": "responses"}
    session.flush([credential])
    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
        settings=settings,
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
    credential = ModelProviderCredentialCommandService(
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
                        "version": agent.version,
                        "instructions": agent.instructions,
                        "model_settings": {"model_api": "chat_completions"},
                        "model": "snapshot-model",
                        "model_provider_credential_id": str(credential.id),
                        "model_api": "chat_completions",
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

    task.input = {"planning_mode": "explicit", "work_packages": task.project_plan["work_packages"]}
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    snapshot = run.input["authorization_snapshot"]["model_provider"]
    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
        settings=settings,
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
    task.input = author_fixture_plan(session, task)
    first_run = service.create_queued_run_for_task(task)
    first_snapshot = first_run.input["authorization_snapshot"]
    first_run.status = RunStatus.COMPLETED.value
    install.status = "disabled"
    session.flush()
    step = session.get(TaskStep, first_run.task_step_id)
    assert step is not None
    second_run = _run_step_launcher(session).create_run_for_step(task, step)
    second_snapshot = second_run.input["authorization_snapshot"]

    assert first_snapshot["installed_skills"][0]["source_checksum"] == "sha256:v1"
    assert second_snapshot["installed_skills"] == []


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
    team = AgentTeam(
        workspace_id=workspace.id, name="Broken plan", manager_agent_profile_id=manager.id
    )
    session.add(team)
    session.flush()
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Build dashboard",
        agent_team_id=team.id,
        team_snapshot={
            "snapshot_version": 1,
            "team": {
                "id": str(team.id),
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
                },
            ]
        },
    )
    task.input = {**task.input, "planning_mode": "explicit"}
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


def test_agent_request_includes_profile_tool_policy_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        model_settings={"model_api": "chat_completions"},
        tool_policy={"allowed_tools": ["send_agent_message", "write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input=_authorized_run_input(session, task, agent),
    )
    session.add(run)
    session.commit()

    job = ExecutionRequest(
        workspace_id=workspace.id,
        kind=ExecutionKind.AGENT,
        resource_id=run.id,
        requested_by_user_id=user.id,
    )
    request = _build_agent_request(session, run, job)

    assert request.context.allowed_tools == ("send_agent_message", "write_artifact")
    assert request.model_api == "chat_completions"
    provider_credential_id = request.context.metadata["model_provider_credential_id"]
    assert isinstance(provider_credential_id, str)
    expected_metadata = {
        "agent_profile_id": str(agent.id),
        "agent_role": "designer",
        "run_model": "gpt-4.1",
        "model_provider_provider": "openai",
        "model_provider_credential_id": provider_credential_id,
        "model_provider_model_api": "chat_completions",
        "authorization_scope": "workspace",
        "authorized_workspace_id": str(workspace.id),
        "authorized_task_id": str(task.id),
        "tool_policy_source": "agent_profile",
        "authorization_snapshot_version": 4,
        "agent_mailbox": {
            "scope": {"task_id": str(task.id)},
            "thread_count": 0,
            "message_count": 0,
            "unread_count": 0,
            "pending_count": 0,
            "latest_unread_messages": [],
        },
    }
    assert request.context.metadata | expected_metadata == request.context.metadata
    assert (
        request.context.metadata["authorization_snapshot_fingerprint"]
        == (run.input["authorization_snapshot"]["fingerprint"])
    )
    assert request.context.metadata["persistent_session_mode"] == "sdk_session"
    assert isinstance(request.context.metadata["persistent_session_key"], str)


def test_agent_request_includes_unread_mailbox_context() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Inbox task",
        status=TaskStatus.QUEUED.value,
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="builder",
        tool_policy={
            "allowed_tools": [
                "get_agent_inbox",
                "list_agent_thread_messages",
                "mark_agent_message_read",
            ]
        },
    )
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
        input=_authorized_run_input(session, task, recipient),
    )
    session.add_all([message, run])
    session.commit()
    job = ExecutionRequest(
        workspace_id=workspace.id,
        kind=ExecutionKind.AGENT,
        resource_id=run.id,
        requested_by_user_id=user.id,
    )

    request = _build_agent_request(session, run, job)

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
    assert "Runtime capabilities and evidence:" in request.input_text
    assert (
        "Available tools: get_agent_inbox, list_agent_thread_messages, mark_agent_message_read"
    ) in request.input_text
    assert "Latest unread mailbox messages:" in request.input_text
    assert "handoff" in request.input_text
    assert "[redacted]" in request.input_text
    assert "hidden-mailbox-token" not in request.input_text
    assert "sk-mailbox-secret" not in request.input_text


def test_agent_request_mailbox_context_is_scoped_to_current_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    current_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Current inbox task",
        status=TaskStatus.QUEUED.value,
    )
    other_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
        input=_authorized_run_input(session, current_task, recipient),
    )
    session.add_all([current_message, other_message, run])
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Team runtime task",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    runtime_state = TeamOperationService(session).get_or_initialize_state(
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
        input=_authorized_run_input(session, task, recipient),
    )
    session.add_all([runtime_message, other_message, run])
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
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
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Build pitch deck",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        instructions="Design assets.",
        tool_policy={"allowed_tools": ["send_agent_message", "write_artifact"]},
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
        input=_authorized_run_input(session, task, agent, step=step),
    )
    session.add(run)
    session.commit()

    job = ExecutionRequest(
        workspace_id=workspace.id,
        kind=ExecutionKind.AGENT,
        resource_id=run.id,
        requested_by_user_id=user.id,
    )
    request = _build_agent_request(session, run, job)

    assert request.context.allowed_tools == ("send_agent_message", "write_artifact")
    expected_metadata = {
        "agent_profile_id": str(agent.id),
        "agent_role": "designer",
        "run_model": "gpt-4.1",
        "model_provider_provider": "openai",
        "model_provider_credential_id": request.context.metadata["model_provider_credential_id"],
        "model_provider_model_api": None,
        "authorization_scope": "workspace",
        "authorized_workspace_id": str(workspace.id),
        "authorized_task_id": str(task.id),
        "tool_policy_source": "agent_profile",
        "authorization_snapshot_version": 4,
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
        status=TaskStatus.QUEUED.value,
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Designer",
        role="designer",
        tool_policy={"allowed_tools": ["write_artifact"]},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input=_authorized_run_input(session, task, agent),
    )
    agent.tool_policy = {"allowed_tools": ["send_agent_message", "write_artifact"]}
    session.add(run)
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
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
    credential = ModelProviderCredentialCommandService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Custom Provider",
        provider="openai",
        api_key="openai-key",
        default_model="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        is_default=True,
        budget_metadata={"model_api": "responses"},
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
        model_settings={"model_api": "responses"},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input=_authorized_run_input(session, task, agent, settings=settings),
    )
    session.add(run)
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
        settings=settings,
    )

    assert request.model == "gpt-4.1-mini"
    assert request.provider == "openai"
    assert request.base_url == "https://api.openai.com/v1"
    assert request.api_key == "openai-key"
    assert request.model_api == "responses"
    assert request.model_provider_credential_id == credential.id
    assert request.context.metadata["run_model"] == "gpt-4.1-mini"
    assert request.context.metadata["model_provider_provider"] == "openai"
    assert request.context.metadata["model_provider_credential_id"] == str(credential.id)
    assert request.context.metadata["model_provider_model_api"] == "responses"
    assert request.tracing is not None
    assert request.tracing.metadata["run_model"] == "gpt-4.1-mini"
    assert request.tracing.metadata["model_provider_provider"] == "openai"
    assert request.tracing.metadata["model_provider_credential_id"] == str(credential.id)
    assert request.tracing.metadata["model_provider_model_api"] == "responses"
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
    credential = ModelProviderCredentialCommandService(
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
        budget_metadata={"model_api": "chat_completions"},
    )
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
        model_settings={"model_api": "responses"},
    )
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input=_authorized_run_input(session, task, agent, settings=settings),
    )
    session.add(run)
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
        settings=settings,
    )

    assert request.model == "router/default"
    assert request.model_api == "responses"
    assert request.context.metadata["model_provider_model_api"] == "responses"
    assert request.tracing is not None
    assert request.tracing.metadata["model_provider_model_api"] == "responses"


def test_agent_request_fails_closed_when_workspace_default_snapshot_becomes_unhealthy() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialCommandService(
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
    snapshot = RunAuthorizationSnapshotService(
        session,
        RunRequestBuilder(session, settings),
    ).model_provider_snapshot(workspace.id, agent)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": _authorization_snapshot(
                session,
                workspace_id=str(workspace.id),
                task_id=str(task.id),
                agent_profile_id=str(agent.id),
                model_provider=snapshot,
            )
        },
    )
    session.add(run)
    session.commit()

    primary.health_status = "unhealthy"
    session.commit()

    with pytest.raises(ValueError, match="not found or unavailable"):
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
            settings=settings,
        )

    assert snapshot["source"] == "workspace_default"
    assert snapshot["credential_id"] == str(primary.id)
    assert backup.status == "active"


def test_agent_request_does_not_fallback_explicit_inactive_provider_override() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    settings = Settings(
        environment="test",
        credential_encryption_secret="test-secret",
        credential_encryption_key_id="test-key",
    )
    service = ModelProviderCredentialCommandService(
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
        input={
            "authorization_snapshot": _authorization_snapshot(
                session,
                workspace_id=str(workspace.id),
                task_id=str(task.id),
                agent_profile_id=str(agent.id),
                model_provider=snapshot,
            )
        },
    )
    session.add(run)
    session.commit()

    inactive.status = "inactive"
    session.commit()

    with pytest.raises(ValueError, match="not found or unavailable"):
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
            settings=settings,
        )

    assert snapshot["source"] == "agent_override"
    assert snapshot["credential_id"] == str(inactive.id)
    assert backup.status == "active"
    assert "sk-inactive-explicit" not in str(snapshot)


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
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
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
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "Run agent profile not found" in str(exc)
    else:
        raise AssertionError("Expected foreign agent profile to be rejected")


def test_agent_request_rejects_task_step_from_another_task() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
    other_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
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
        input={
            "authorization_snapshot": _authorization_snapshot(
                session,
                workspace_id=str(workspace.id),
                task_id=str(task.id),
                task_step_id=str(step.id),
                agent_profile_id=str(agent.id),
            )
        },
    )
    session.add(run)
    session.commit()

    try:
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "task step does not belong" in str(exc)
    else:
        raise AssertionError("Expected foreign task step to be rejected")


def test_agent_request_rejects_worker_job_scope_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
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
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=uuid4(),
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "resource does not match" in str(exc)
    else:
        raise AssertionError("Expected worker job resource mismatch to be rejected")


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        ({}, "Authorization snapshot is required"),
        ({"version": 1}, "Authorization snapshot version is unsupported"),
    ],
)
def test_agent_request_rejects_missing_or_obsolete_authorization_snapshot(
    snapshot: dict[str, object],
    message: str,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Denied run",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.flush()
    with pytest.raises(ValueError, match=message):
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )


def test_agent_request_rejects_authorization_snapshot_scope_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
    agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="writer")
    session.add_all([task, agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={
            "authorization_snapshot": _authorization_snapshot(
                session,
                workspace_id=str(uuid4()),
                task_id=str(task.id),
                agent_profile_id=str(agent.id),
            )
        },
    )
    session.add(run)
    session.commit()

    try:
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "Authorization snapshot workspace_id mismatch" in str(exc)
    else:
        raise AssertionError("Expected authorization snapshot mismatch to be rejected")


def test_agent_request_rejects_authorization_snapshot_tool_escalation() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
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
            "authorization_snapshot": _authorization_snapshot(
                session,
                workspace_id=str(workspace.id),
                task_id=str(task.id),
                agent_profile_id=str(agent.id),
                allowed_tools=["write_artifact", "delete_workspace_file"],
            )
        },
    )
    session.add(run)
    session.commit()

    try:
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "Authorization snapshot tool manifest mismatch" in str(exc)
    else:
        raise AssertionError("Expected tool escalation snapshot to be rejected")


def test_agent_request_rejects_authorization_snapshot_installed_skill_tampering() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Writer",
        role="writer",
        skills={"installed_skill_ids": []},
    )
    session.add_all([task, agent])
    session.flush()
    snapshot = _authorization_snapshot(
        session,
        workspace_id=str(workspace.id),
        task_id=str(task.id),
        agent_profile_id=str(agent.id),
        installed_skills=[],
    )
    snapshot["installed_skills"] = [{"install_id": str(uuid4())}]
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()

    try:
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "fingerprint mismatch" in str(exc)
    else:
        raise AssertionError("Expected installed skill snapshot tampering to be rejected")


def test_agent_request_rejects_authorization_snapshot_skill_provenance_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        owner_agent_profile_id=test_agent_id(session, workspace.id),
        created_by_user_id=user.id,
        title="Draft report",
    )
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
    snapshot = _authorization_snapshot(
        session,
        workspace_id=str(workspace.id),
        task_id=str(task.id),
        agent_profile_id=str(agent.id),
        installed_skills=[
            {
                "install_id": str(install.id),
                "source_skill_id": str(skill.id),
                "installed_key": "writer-pro",
                "installed_name": "Writer Pro",
                "installed_version": "1.0.0",
                "installed_capability_keys": ["writing"],
                "source_checksum": "sha256:v1",
                "source_visibility": "public",
            }
        ],
    )
    installed_skills = snapshot["installed_skills"]
    assert isinstance(installed_skills, list)
    assert isinstance(installed_skills[0], dict)
    installed_skills[0]["source_checksum"] = "sha256:tampered"
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()

    try:
        _build_agent_request(
            session,
            run,
            ExecutionRequest(
                workspace_id=workspace.id,
                kind=ExecutionKind.AGENT,
                resource_id=run.id,
                requested_by_user_id=user.id,
            ),
        )
    except ValueError as exc:
        assert "fingerprint mismatch" in str(exc)
    else:
        raise AssertionError("Expected skill provenance tampering to be rejected")


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
        status="active",
    )
    session.add(runtime)
    session.flush()
    team.default_task_policy = {
        "team_runtime": {
            "status": "running",
            "workspace_runtime_id": str(runtime.id),
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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="First market question",
    )
    second_task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
    launcher = _run_step_launcher(session)
    first_run = launcher.create_run_for_step(first_task, first_step)
    second_run = launcher.create_run_for_step(second_task, second_step)

    first_request = _build_agent_request(
        session,
        first_run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=first_run.id,
            requested_by_user_id=user.id,
        ),
    )
    second_request = _build_agent_request(
        session,
        second_run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=second_run.id,
            requested_by_user_id=user.id,
        ),
    )

    assert first_request.session is not None
    assert second_request.session is not None
    assert first_request.session.session_id == second_request.session.session_id
    assert (
        first_request.session.session_id
        == f"{workspace.id}:{user.id}:team_agent:{team.id}:{agent.id}"
    )
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
    assert first_request.tracing.workflow_name == "opsmesh.team_agent_run"
    assert first_request.tracing.group_id == first_request.session.session_id
    assert first_request.tracing.metadata["team"]["team_id"] == str(team.id)
    assert first_request.tracing.metadata["team"]["current_member"]["team_role"] == "Research"
    assert first_request.tracing.metadata["team"]["runtime"] == {
        "status": "running",
        "workspace_runtime_id": str(runtime.id),
        "runtime_status": "active",
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


def test_agent_request_injects_only_resource_authorized_memory() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    foreign_user = User(email="foreign-memory@example.com", display_name="Foreign")
    foreign_workspace = Workspace(
        owner=foreign_user,
        name="Foreign",
        slug="foreign-memory",
        settings={},
    )
    session.add_all(
        [
            foreign_user,
            foreign_workspace,
            WorkspaceMember(
                workspace=foreign_workspace,
                user=foreign_user,
                role="owner",
            ),
        ]
    )
    session.flush()
    memory_resource = CapabilityResource(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        key="team-runtime-memory",
        name="Team runtime memory",
        resource_type="memory_collection",
        access_mode="read",
        locator={
            "source_types": ["semantic_memory"],
            "tags": ["runtime"],
            "scope_types": ["team"],
            "scope_ids": [],
        },
    )
    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Runtime Builder",
        role="builder",
    )
    team = AgentTeam(workspace_id=workspace.id, name="Runtime Team", team_type="delivery")
    other_team = AgentTeam(
        workspace_id=workspace.id,
        name="Other Team",
        team_type="delivery",
    )
    session.add_all([memory_resource, profile, team, other_team])
    session.flush()
    memory_resource.locator = {
        **memory_resource.locator,
        "scope_ids": [str(team.id)],
    }
    profile.capabilities = {"resource_ids": [str(memory_resource.id)]}
    task = Task(
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Deployment rollback procedure",
        description="Recover a failed runtime deployment safely.",
    )
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=profile.id,
                team_role="Builder",
            ),
            task,
            _semantic_memory_entry(
                workspace_id=workspace.id,
                scope_id=team.id,
                title="Authorized rollback memory",
                content="Deployment rollback requires draining the runtime queue first.",
                tags=["runtime"],
            ),
            _semantic_memory_entry(
                workspace_id=workspace.id,
                scope_id=other_team.id,
                title="Other team rollback memory",
                content="Other team deployment rollback material must remain private.",
                tags=["runtime"],
                metadata={
                    "scope_type": "team",
                    "scope_id": str(team.id),
                    "tags": ["runtime"],
                },
            ),
            _semantic_memory_entry(
                workspace_id=foreign_workspace.id,
                scope_id=uuid4(),
                title="Foreign workspace rollback memory",
                content="Foreign deployment rollback material must remain private.",
                tags=["runtime"],
            ),
        ]
    )
    session.flush()
    snapshot = RunAuthorizationSnapshotService(
        session,
        RunRequestBuilder(session, None),
    ).build_authorization_snapshot(task, None, profile)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=profile.id,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()

    request = _build_agent_request(
        session,
        run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=run.id,
            requested_by_user_id=user.id,
        ),
    )

    assert "Authorized memory context" in request.input_text
    assert "Authorized rollback memory" in request.input_text
    assert "Other team rollback memory" not in request.input_text
    assert "Foreign workspace rollback memory" not in request.input_text
    evidence = request.context.metadata["memory_retrieval"]
    assert isinstance(evidence, dict)
    assert evidence["status"] == "completed"
    assert evidence["rendered_count"] == 1
    assert evidence["context_status"] == "included"
    assert evidence["selected"][0]["scope_id"] == str(team.id)
    retrieval = session.scalars(select(WorkspaceMemoryRetrievalEvent)).one()
    assert retrieval.workspace_id == workspace.id
    assert retrieval.agent_run_id == run.id
    assert retrieval.scope_filters["access_scopes"][0]["resource_id"] == str(memory_resource.id)


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
        execution_identity=ExecutionIdentityService(session).capture(workspace.id, user.id),
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
    launcher = _run_step_launcher(session)
    planner_run = launcher.create_run_for_step(task, planner_step)
    builder_run = launcher.create_run_for_step(task, builder_step)
    session.commit()

    planner_request = _build_agent_request(
        session,
        planner_run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=planner_run.id,
            requested_by_user_id=user.id,
        ),
    )
    assert planner_request.context.allowed_tools == (
        "get_agent_inbox",
        "mark_agent_message_read",
        "send_agent_message",
    )
    assert planner_request.session is not None
    send_result = execute_tool(
        AgentToolExecutionService(session),
        context=planner_request.context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(builder.id),
            "subject": "Builder handoff",
            "body": "Start with the runtime ensure path.",
        },
        tool_call_id="test-builder-handoff",
        approval_granted=False,
    )
    assert send_result.status == "completed"
    assert send_result.output is not None
    assert (
        send_result.output["thread"]["id"]
        == (planner_request.context.metadata["agent_mailbox"]["scope"]["thread_id"])
    )
    assert send_result.output["thread"]["task_id"] is None
    assert send_result.output["thread"]["agent_team_id"] == str(team.id)
    assert send_result.output["message"]["task_id"] is None
    assert send_result.output["message"]["agent_team_id"] == str(team.id)

    builder_request = _build_agent_request(
        session,
        builder_run,
        ExecutionRequest(
            workspace_id=workspace.id,
            kind=ExecutionKind.AGENT,
            resource_id=builder_run.id,
            requested_by_user_id=user.id,
        ),
    )
    assert builder_request.session is not None
    assert (
        builder_request.session.session_id
        == f"{workspace.id}:{user.id}:team_agent:{team.id}:{builder.id}"
    )
    assert builder_request.context.metadata["agent_mailbox"]["unread_count"] == 1
    assert (
        builder_request.context.metadata["agent_mailbox"]["latest_unread_messages"][0][
            "body_preview"
        ]
        == "Start with the runtime ensure path."
    )
    assert builder_request.context.metadata["team_context"]["team_name"] == "Product Team"
    assert "Team context:" in builder_request.input_text
    assert "Start with the runtime ensure path." in (builder_request.input_text)
    assert "Available tools:" in builder_request.input_text
    assert "get_agent_inbox" in builder_request.input_text
    assert "send_agent_message" in builder_request.input_text
    assert "Latest unread mailbox messages:" in builder_request.input_text
    assert "Start with the runtime ensure path." in builder_request.input_text
