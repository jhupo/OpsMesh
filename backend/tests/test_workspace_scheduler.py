from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.orchestration.scheduler import WorkspaceScheduler
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime_spaces.service import RuntimeSpaceService
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)
from backend.app.workspaces.quotas import WorkspaceQuotaService


def test_workspace_scheduler_orders_steps_by_task_priority_and_run_quota() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 1}},
    )
    low_task, low_step = _seed_task_step(session, workspace, title="Low", priority=1)
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[low_step, high_step],
    )

    assert decision.available_run_slots == 1
    assert [step.task_id for step in decision.runnable_steps] == [high_task.id]
    assert [step.task_id for step in decision.blocked_steps] == [low_task.id]
    assert low_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"
    assert high_step.dependencies["priority_score"] == 10
    assert high_step.dependencies["scheduled_at"]


def test_workspace_scheduler_round_robins_between_tasks_before_extra_parallel_steps() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 3}},
    )
    first_task, first_step = _seed_task_step(session, workspace, title="First", priority=10)
    _, first_extra_step = _seed_extra_step(session, first_task, title="First extra", order_index=1)
    second_task, second_step = _seed_task_step(session, workspace, title="Second", priority=8)
    third_task, third_step = _seed_task_step(session, workspace, title="Third", priority=7)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[first_step, first_extra_step, second_step, third_step],
    )

    assert [step.task_id for step in decision.runnable_steps] == [
        first_task.id,
        second_task.id,
        third_task.id,
    ]
    assert decision.blocked_steps == (first_extra_step,)
    assert first_extra_step.dependencies["blocked_reason"] == "workspace_run_quota_exceeded"


def test_workspace_scheduler_can_allow_multiple_steps_per_task_per_tick() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={
            "scheduler": {
                "max_active_runs": 3,
                "max_steps_per_task_per_tick": 2,
            }
        },
    )
    first_task, first_step = _seed_task_step(session, workspace, title="First", priority=10)
    _, first_extra_step = _seed_extra_step(session, first_task, title="First extra", order_index=1)
    second_task, second_step = _seed_task_step(session, workspace, title="Second", priority=8)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[first_step, first_extra_step, second_step],
    )

    assert [step.id for step in decision.runnable_steps] == [
        first_step.id,
        first_extra_step.id,
        second_step.id,
    ]
    assert decision.blocked_steps == ()


def test_workspace_scheduler_task_quota_allows_highest_priority_new_tasks() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_running_tasks": 1}},
    )
    low_task, low_step = _seed_task_step(session, workspace, title="Low", priority=1)
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[low_step, high_step],
    )

    assert [step.task_id for step in decision.runnable_steps] == [high_task.id]
    assert decision.blocked_steps == (low_step,)
    assert decision.blocked_reason == "workspace_task_quota_exceeded"
    assert low_step.dependencies["blocked_reason"] == "workspace_task_quota_exceeded"


def test_workspace_scheduler_task_quota_keeps_existing_active_task_eligible() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_running_tasks": 1}},
    )
    active_task, active_next_step = _seed_task_step(
        session,
        workspace,
        title="Active",
        priority=1,
    )
    active_run = AgentRun(
        workspace_id=workspace.id,
        task_id=active_task.id,
        status=RunStatus.RUNNING.value,
    )
    high_task, high_step = _seed_task_step(session, workspace, title="High", priority=10)
    session.add(active_run)
    session.flush()

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[active_next_step, high_step],
    )

    assert decision.runnable_steps == (active_next_step,)
    assert decision.blocked_steps == (high_step,)
    assert high_step.dependencies["blocked_reason"] == "workspace_task_quota_exceeded"


def test_workspace_scheduler_boosts_long_waiting_lower_priority_work() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={
            "scheduler": {
                "max_active_runs": 1,
                "starvation_boost_after_seconds": 60,
            }
        },
    )
    low_task, low_step = _seed_task_step(session, workspace, title="Old low", priority=1)
    high_task, high_step = _seed_task_step(session, workspace, title="New high", priority=10)
    old_created_at = datetime.now(UTC) - timedelta(minutes=20)
    low_step.created_at = old_created_at
    low_task.created_at = old_created_at
    session.flush()

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[high_step, low_step],
    )

    assert decision.runnable_steps == (low_step,)
    assert decision.blocked_steps == (high_step,)
    assert low_step.dependencies["priority_score"] > high_step.dependencies["priority_score"]
    assert low_step.dependencies["scheduled_at"]
    assert high_step.dependencies["priority_score"] == 10


def test_workspace_scheduler_blocks_steps_exceeding_tick_resource_limits() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={
            "scheduler": {
                "max_active_runs": 2,
                "resource_limits": {"memory_mb": 4096, "cpu": 4},
            }
        },
    )
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="First render",
        priority=10,
        dependencies={"resource_requirements": {"memory_mb": 3072, "cpu": 2}},
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Second render",
        priority=9,
        dependencies={"resource_requirements": {"memory_mb": 2048, "cpu": 2}},
    )

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[second_step, first_step],
    )

    assert decision.runnable_steps == (first_step,)
    assert decision.blocked_steps == (second_step,)
    assert decision.blocked_reason == "workspace_resource_quota_exceeded"
    assert first_step.dependencies["resource_requirements"] == {"memory_mb": 3072, "cpu": 2}
    assert first_step.dependencies["priority_score"] == 10
    assert first_step.dependencies["scheduled_at"]
    assert second_step.dependencies["blocked_reason"] == "workspace_resource_quota_exceeded"
    assert second_step.dependencies["blocked_resource_keys"] == ["memory_mb"]
    assert {first_task.id, second_task.id} == {step.task_id for step in [first_step, second_step]}


def test_workspace_scheduler_blocks_when_active_run_quota_is_full() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 1}},
    )
    task, step = _seed_task_step(session, workspace, title="Blocked", priority=1)
    session.add(
        AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            status=RunStatus.RUNNING.value,
        )
    )
    session.flush()

    decision = WorkspaceScheduler(session).select_runnable_steps(
        workspace_id=workspace.id,
        candidate_steps=[step],
    )

    assert decision.runnable_steps == ()
    assert decision.blocked_steps == (step,)
    assert decision.blocked_reason == "workspace_run_quota_exceeded"
    assert step.dependencies["scheduling_status"] == "blocked"


def test_workspace_scheduler_pause_blocks_new_steps() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={
            "scheduler": {
                "paused": True,
                "pause_reason": "operator_review",
                "max_active_runs": 10,
            }
        },
    )
    _, step = _seed_task_step(session, workspace, title="Paused", priority=10)

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    assert runs == []
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "operator_review"


def test_run_orchestration_reserves_runtime_space_capacity_before_enqueue() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 10}},
    )
    runtime_space = _seed_runtime_space(session, workspace, active_runs=1)
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="High",
        priority=10,
        runtime_space_id=runtime_space.id,
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Low",
        priority=1,
        runtime_space_id=runtime_space.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    quota = _runtime_space_quota(session, runtime_space.id)
    reservations = _runtime_space_reservations(session, runtime_space.id)

    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert quota.reserved_value == 1
    assert len(reservations) == 1
    assert reservations[0].agent_run_id == runs[0].id
    assert reservations[0].status == "active"
    assert first_step.dependencies["priority_score"] == 10
    assert first_step.dependencies["scheduled_at"]
    assert second_step.dependencies["scheduling_status"] == "blocked"
    assert second_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:active_runs"

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert _runtime_space_quota(session, runtime_space.id).reserved_value == 1
    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    assert "blocked_reason" not in second_step.dependencies
    assert second_step.dependencies["scheduled_at"]
    cancelled_step = session.get(TaskStep, first_step.id)
    assert cancelled_step is not None
    assert cancelled_step.status == "cancelled"
    released = [
        reservation
        for reservation in _runtime_space_reservations(session, runtime_space.id)
        if reservation.task_id == first_task.id
    ][0]
    assert released.status == "released"
    assert released.released_at is not None


def test_run_orchestration_blocks_paused_runtime_space_without_reserving_capacity() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, settings={"scheduler": {"max_active_runs": 10}})
    runtime_space = _seed_runtime_space(session, workspace, active_runs=1)
    runtime_space.status = "paused"
    _, step = _seed_task_step(
        session,
        workspace,
        title="Paused space",
        priority=10,
        runtime_space_id=runtime_space.id,
    )
    session.flush()

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    assert runs == []
    assert _runtime_space_quota(session, runtime_space.id).reserved_value == 0
    assert _runtime_space_reservations(session, runtime_space.id) == []
    assert step.dependencies["scheduling_status"] == "blocked"
    assert step.dependencies["blocked_reason"] == "runtime_space_paused"


def test_run_orchestration_reserves_runtime_space_resource_requirements() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 10}},
    )
    runtime_space = _seed_runtime_space(
        session,
        workspace,
        active_runs=10,
        quota_limits={"memory_mb": 4096, "cpu": 4},
        policy={"resource_requirements": {"memory_mb": 2048, "cpu": 1}},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Image Worker",
        role="designer",
        runtime_policy={"resource_requirements": {"memory_mb": 4096, "cpu": 2}},
    )
    session.add(agent)
    session.flush()
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="Render high",
        priority=10,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
        dependencies={"resource_requirements": {"cpu": 3}},
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Render low",
        priority=1,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    memory_quota = _runtime_space_quota(session, runtime_space.id, "memory_mb")
    cpu_quota = _runtime_space_quota(session, runtime_space.id, "cpu")
    reservations = _runtime_space_reservations(session, runtime_space.id)

    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert memory_quota.reserved_value == 4096
    assert cpu_quota.reserved_value == 3
    assert reservations[0].resource_usage == {"active_runs": 1, "memory_mb": 4096, "cpu": 3}
    assert first_step.dependencies["resource_requirements"] == {"cpu": 3}
    assert first_step.dependencies["priority_score"] == 10
    assert first_step.dependencies["scheduled_at"]
    assert second_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:memory_mb"

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    assert _runtime_space_quota(session, runtime_space.id, "memory_mb").reserved_value == 4096
    assert _runtime_space_quota(session, runtime_space.id, "cpu").reserved_value == 2


def test_runtime_space_reservation_is_idempotent_for_same_key() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    runtime_space = _seed_runtime_space(
        session,
        workspace,
        active_runs=2,
        quota_limits={"memory_mb": 4096},
    )

    service = RuntimeSpaceService(session)
    first = service.reserve_run_capacity(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:run",
        resource_usage={"active_runs": 1, "memory_mb": 2048},
    )
    second = service.reserve_run_capacity(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:run",
        resource_usage={"active_runs": 1, "memory_mb": 2048},
    )

    assert first.reservation is not None
    assert second.reservation is not None
    assert second.reservation.id == first.reservation.id
    assert _runtime_space_quota(session, runtime_space.id, "active_runs").reserved_value == 1
    assert _runtime_space_quota(session, runtime_space.id, "memory_mb").reserved_value == 2048
    assert len(_runtime_space_reservations(session, runtime_space.id)) == 1


def test_runtime_space_reservation_rejects_same_key_with_different_usage() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    runtime_space = _seed_runtime_space(
        session,
        workspace,
        active_runs=2,
        quota_limits={"memory_mb": 4096},
    )

    service = RuntimeSpaceService(session)
    first = service.reserve_run_capacity(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:run",
        resource_usage={"active_runs": 1, "memory_mb": 1024},
    )
    second = service.reserve_run_capacity(
        workspace_id=workspace.id,
        runtime_space_id=runtime_space.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:run",
        resource_usage={"active_runs": 1, "memory_mb": 2048},
    )

    assert first.reservation is not None
    assert second.reservation is None
    assert second.blocked_reason == "runtime_space_reservation_conflict"
    assert _runtime_space_quota(session, runtime_space.id, "active_runs").reserved_value == 1
    assert _runtime_space_quota(session, runtime_space.id, "memory_mb").reserved_value == 1024
    assert len(_runtime_space_reservations(session, runtime_space.id)) == 1


def test_run_orchestration_reserves_and_releases_workspace_quota() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    session.add_all(
        [
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="active_runs",
                limit_value=1,
            ),
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="memory_mb",
                limit_value=4096,
            ),
        ]
    )
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="First",
        priority=10,
        dependencies={"resource_requirements": {"memory_mb": 3072}},
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Second",
        priority=9,
        dependencies={"resource_requirements": {"memory_mb": 2048}},
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    active_quota = _workspace_quota(session, workspace.id, "active_runs")
    memory_quota = _workspace_quota(session, workspace.id, "memory_mb")
    reservations = _workspace_reservations(session, workspace.id)
    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert active_quota.reserved_value == 1
    assert memory_quota.reserved_value == 3072
    assert reservations[0].agent_run_id == runs[0].id
    assert reservations[0].resource_usage == {"active_runs": 1, "memory_mb": 3072}
    assert second_step.dependencies["blocked_reason"] == "workspace_quota_exceeded:active_runs"

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    assert _workspace_quota(session, workspace.id, "active_runs").reserved_value == 1
    assert _workspace_quota(session, workspace.id, "memory_mb").reserved_value == 2048
    released = [
        reservation
        for reservation in _workspace_reservations(session, workspace.id)
        if reservation.task_id == first_task.id
    ][0]
    assert released.status == "released"
    assert released.released_at is not None


def test_workspace_quota_reservation_rejects_same_key_with_different_usage() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    session.add_all(
        [
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="active_runs",
                limit_value=2,
            ),
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="memory_mb",
                limit_value=4096,
            ),
        ]
    )
    session.flush()

    service = WorkspaceQuotaService(session)
    first = service.reserve(
        workspace_id=workspace.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:workspace_run",
        resource_usage={"active_runs": 1, "memory_mb": 1024},
    )
    second = service.reserve(
        workspace_id=workspace.id,
        task_id=None,
        task_step_id=None,
        reservation_key="task_step:stable:workspace_run",
        resource_usage={"active_runs": 1, "memory_mb": 2048},
    )

    assert first.reservation is not None
    assert second.reservation is None
    assert second.blocked_reason == "workspace_reservation_conflict"
    assert _workspace_quota(session, workspace.id, "active_runs").reserved_value == 1
    assert _workspace_quota(session, workspace.id, "memory_mb").reserved_value == 1024
    assert len(_workspace_reservations(session, workspace.id)) == 1


def test_run_orchestration_enforces_workspace_runtime_slot_quotas() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    session.add_all(
        [
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="active_runs",
                limit_value=10,
            ),
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="docker_runtimes",
                limit_value=1,
            ),
            WorkspaceQuota(
                workspace_id=workspace.id,
                quota_key="self_hosted_jobs",
                limit_value=1,
            ),
        ]
    )
    runtime_space = _seed_runtime_space(
        session,
        workspace,
        active_runs=10,
        policy={"workspace_reservation_usage": {"docker_runtimes": 1}},
    )
    agent = AgentProfile(
        workspace_id=workspace.id,
        name="Builder",
        role="builder",
        runtime_policy={"reservation_usage": {"self_hosted_jobs": 1}},
    )
    session.add(agent)
    session.flush()
    first_task, first_step = _seed_task_step(
        session,
        workspace,
        title="First",
        priority=10,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
    )
    second_task, second_step = _seed_task_step(
        session,
        workspace,
        title="Second",
        priority=9,
        runtime_space_id=runtime_space.id,
        assigned_agent_profile_id=agent.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    reservations = _workspace_reservations(session, workspace.id)
    assert len(runs) == 1
    assert runs[0].task_id == first_task.id
    assert reservations[0].resource_usage == {
        "active_runs": 1,
        "docker_runtimes": 1,
        "self_hosted_jobs": 1,
    }
    assert _workspace_quota(session, workspace.id, "docker_runtimes").reserved_value == 1
    assert _workspace_quota(session, workspace.id, "self_hosted_jobs").reserved_value == 1
    assert second_step.dependencies["blocked_reason"] == (
        "workspace_quota_exceeded:docker_runtimes"
    )

    RunOrchestrationService(session)._mark_run_cancelled(
        runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    assert len(next_runs) == 1
    assert next_runs[0].task_id == second_task.id
    released = [
        reservation
        for reservation in _workspace_reservations(session, workspace.id)
        if reservation.task_id == first_task.id
    ][0]
    assert released.status == "released"
    assert _workspace_quota(session, workspace.id, "docker_runtimes").reserved_value == 1
    assert _workspace_quota(session, workspace.id, "self_hosted_jobs").reserved_value == 1


def test_run_orchestration_reorders_released_quota_with_new_high_priority_task() -> None:
    session = _session()
    _, workspace = _seed_workspace(
        session,
        settings={"scheduler": {"max_active_runs": 10}},
    )
    runtime_space = _seed_runtime_space(session, workspace, active_runs=1)
    running_task, running_step = _seed_task_step(
        session,
        workspace,
        title="Running",
        priority=5,
        runtime_space_id=runtime_space.id,
    )
    low_task, low_step = _seed_task_step(
        session,
        workspace,
        title="Low",
        priority=1,
        runtime_space_id=runtime_space.id,
    )

    first_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert len(first_runs) == 1
    assert first_runs[0].task_id == running_task.id
    assert low_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:active_runs"

    high_task, high_step = _seed_task_step(
        session,
        workspace,
        title="Urgent",
        priority=20,
        runtime_space_id=runtime_space.id,
    )
    RunOrchestrationService(session)._mark_run_cancelled(
        first_runs[0],
        completed_at=datetime.now(UTC),
    )
    next_runs = RunOrchestrationService(session).schedule_workspace_steps(
        workspace_id=workspace.id,
    )

    assert len(next_runs) == 1
    assert next_runs[0].task_id == high_task.id
    assert "blocked_reason" not in high_step.dependencies
    assert high_step.dependencies["scheduled_at"]
    assert low_step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:active_runs"
    assert running_step.status == "cancelled"


def test_workspace_quota_reservation_releases_when_runtime_space_blocks() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    session.add(
        WorkspaceQuota(
            workspace_id=workspace.id,
            quota_key="active_runs",
            limit_value=1,
        )
    )
    runtime_space = _seed_runtime_space(session, workspace, active_runs=0)
    _, step = _seed_task_step(
        session,
        workspace,
        title="Blocked runtime space",
        priority=10,
        runtime_space_id=runtime_space.id,
    )

    runs = RunOrchestrationService(session).schedule_workspace_steps(workspace_id=workspace.id)

    assert runs == []
    assert _workspace_quota(session, workspace.id, "active_runs").reserved_value == 0
    reservation = _workspace_reservations(session, workspace.id)[0]
    assert reservation.status == "released"
    assert step.dependencies["blocked_reason"] == "runtime_space_quota_exceeded:active_runs"


def _seed_task_step(
    session: Session,
    workspace: Workspace,
    *,
    title: str,
    priority: int,
    runtime_space_id: UUID | None = None,
    assigned_agent_profile_id: UUID | None = None,
    dependencies: dict[str, object] | None = None,
) -> tuple[Task, TaskStep]:
    task = Task(
        workspace_id=workspace.id,
        title=title,
        priority=priority,
        status=TaskStatus.QUEUED.value,
        runtime_space_id=runtime_space_id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title=f"{title} step",
        status="queued",
        order_index=0,
        runtime_space_id=runtime_space_id,
        assigned_agent_profile_id=assigned_agent_profile_id,
        dependencies=dependencies or {},
    )
    session.add(step)
    session.flush()
    return task, step


def _seed_extra_step(
    session: Session,
    task: Task,
    *,
    title: str,
    order_index: int,
    dependencies: dict[str, object] | None = None,
) -> tuple[Task, TaskStep]:
    step = TaskStep(
        workspace_id=task.workspace_id,
        task_id=task.id,
        title=title,
        status="queued",
        order_index=order_index,
        runtime_space_id=task.runtime_space_id,
        dependencies=dependencies or {},
    )
    session.add(step)
    session.flush()
    return task, step


def _seed_runtime_space(
    session: Session,
    workspace: Workspace,
    *,
    active_runs: int,
    quota_limits: dict[str, int] | None = None,
    policy: dict[str, object] | None = None,
) -> RuntimeSpace:
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Team space",
        scope="team",
        policy=policy or {},
    )
    session.add(runtime_space)
    session.flush()
    quotas = {"active_runs": active_runs} | (quota_limits or {})
    for quota_key, limit_value in quotas.items():
        session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
                quota_key=quota_key,
                limit_value=limit_value,
            )
        )
    session.flush()
    return runtime_space


def _runtime_space_quota(
    session: Session,
    runtime_space_id: UUID,
    quota_key: str = "active_runs",
) -> RuntimeSpaceQuota:
    quota = session.scalar(
        select(RuntimeSpaceQuota).where(
            RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
            RuntimeSpaceQuota.quota_key == quota_key,
        )
    )
    assert quota is not None
    return quota


def _runtime_space_reservations(
    session: Session,
    runtime_space_id: UUID,
) -> list[RuntimeSpaceReservation]:
    return list(
        session.scalars(
            select(RuntimeSpaceReservation)
            .where(RuntimeSpaceReservation.runtime_space_id == runtime_space_id)
            .order_by(RuntimeSpaceReservation.created_at.asc())
        ).all()
    )


def _workspace_quota(
    session: Session,
    workspace_id: UUID,
    quota_key: str,
) -> WorkspaceQuota:
    quota = session.scalar(
        select(WorkspaceQuota).where(
            WorkspaceQuota.workspace_id == workspace_id,
            WorkspaceQuota.quota_key == quota_key,
        )
    )
    assert quota is not None
    return quota


def _workspace_reservations(
    session: Session,
    workspace_id: UUID,
) -> list[WorkspaceReservation]:
    return list(
        session.scalars(
            select(WorkspaceReservation)
            .where(WorkspaceReservation.workspace_id == workspace_id)
            .order_by(WorkspaceReservation.created_at.asc())
        ).all()
    )


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    settings: dict[str, object] | None = None,
) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(
        owner=user,
        name="Acme",
        slug=str(uuid4()),
        settings=settings or {},
    )
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
