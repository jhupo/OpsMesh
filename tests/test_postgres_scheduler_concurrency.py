import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.scheduling.activities import ScheduleActivities
from opsmesh.orchestration.scheduling.contracts import ScheduleOccurrence
from opsmesh.orchestration.scheduling.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.orchestration.tasks.state import TaskStatus
from opsmesh.runtime.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.execution import test_agent_id
from tests.fixtures.model_provider import seed_default_model_provider

POSTGRES_TEST_URL_ENV = "OPSMESH_TEST_POSTGRES_URL"

pytestmark = pytest.mark.skipif(
    not os.getenv(POSTGRES_TEST_URL_ENV),
    reason=f"set {POSTGRES_TEST_URL_ENV} to run PostgreSQL concurrency tests",
)


@dataclass(frozen=True)
class SchedulerFixture:
    workspace_id: UUID
    runtime_space_id: UUID
    first_step_id: UUID
    second_step_id: UUID


def test_parallel_schedulers_do_not_over_reserve_runtime_space_quota() -> None:
    if _metadata_has_sqlite_json_columns():
        pytest.skip("PostgreSQL concurrency test must run before SQLite metadata patching")

    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        fixture = _seed_scheduler_fixture(session_factory)
        barrier = threading.Barrier(2)

        def schedule_once() -> list[UUID]:
            with session_factory() as session:
                barrier.wait(timeout=10)
                runs = RunOrchestrationService(session).schedule_workspace_steps(
                    workspace_id=fixture.workspace_id,
                )
                session.commit()
                return [run.id for run in runs]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(schedule_once) for _ in range(2)]
            scheduled_run_ids = [run_id for future in futures for run_id in future.result(20)]

        with session_factory() as session:
            runs = list(
                session.scalars(
                    select(AgentRun)
                    .where(AgentRun.workspace_id == fixture.workspace_id)
                    .order_by(AgentRun.created_at.asc())
                ).all()
            )
            active_reservations = list(
                session.scalars(
                    select(RuntimeSpaceReservation).where(
                        RuntimeSpaceReservation.runtime_space_id == fixture.runtime_space_id,
                        RuntimeSpaceReservation.status == "active",
                    )
                ).all()
            )
            quota = session.scalar(
                select(RuntimeSpaceQuota).where(
                    RuntimeSpaceQuota.runtime_space_id == fixture.runtime_space_id,
                    RuntimeSpaceQuota.quota_key == "active_runs",
                )
            )
            assert len(runs) == 1, [step.dependencies for step in session.scalars(select(TaskStep))]

        assert quota is not None
        assert quota.reserved_value == 1
        assert len(runs) == 1
        assert len(scheduled_run_ids) == 1
        assert runs[0].id == scheduled_run_ids[0]
        assert runs[0].status == RunStatus.QUEUED.value
        assert runs[0].task_step_id in {fixture.first_step_id, fixture.second_step_id}
        assert len(active_reservations) == 1
        assert active_reservations[0].agent_run_id == runs[0].id
        assert active_reservations[0].resource_usage == {"active_runs": 1}


def test_parallel_temporal_schedule_occurrences_do_not_duplicate_actions() -> None:
    if _metadata_has_sqlite_json_columns():
        pytest.skip("PostgreSQL concurrency test must run before SQLite metadata patching")
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(UTC)
        workspace_id, schedule_id = _seed_due_scheduled_job(factory, now=now)
        occurrence = ScheduleOccurrence(
            request=ExecutionRequest(
                workspace_id=workspace_id,
                resource_id=schedule_id,
                kind=ExecutionKind.SCHEDULE_TICK,
                parameters={"owner_kind": "scheduled_job"},
            ),
            occurrence_id="one-native-occurrence",
            started_at=now,
        )
        barrier = threading.Barrier(2)
        services = ActivityDependencies(sessions=factory, settings=Settings(environment="test"))
        # Materialization is the synchronous DB boundary also used by the Activity.
        scheduler = ScheduleActivities(services, None)

        def materialize() -> None:
            barrier.wait(timeout=10)
            scheduler._materialize(occurrence)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(materialize) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        with factory() as session:
            events = list(
                session.scalars(
                    select(WorkspaceScheduledJobEvent).where(
                        WorkspaceScheduledJobEvent.scheduled_job_id == schedule_id
                    )
                )
            )
            dispatches = list(
                session.scalars(
                    select(TemporalOutboxEntry).where(
                        TemporalOutboxEntry.workspace_id == workspace_id
                    )
                )
            )
            job = session.get(WorkspaceScheduledJob, schedule_id)
            assert job.status == "completed"
            assert len(events) == 1
            assert events[0].status == "dispatched"
            assert len(dispatches) == 1
            assert dispatches[0].payload["execution"]["resource_id"] == str(job.resource_id)


def _seed_scheduler_fixture(session_factory: sessionmaker[Session]) -> SchedulerFixture:
    with session_factory() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(
            owner=user,
            name="Concurrent Workspace",
            slug=f"ws-{uuid4()}",
            settings={"scheduler": {"max_runs_to_start_per_tick": 2}},
        )
        membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
        session.add_all([user, workspace, membership])
        session.flush()
        seed_default_model_provider(session, workspace_id=workspace.id, user_id=user.id)
        runtime_space = RuntimeSpace(
            workspace_id=workspace.id,
            name="Workspace runtime space",
            scope="workspace",
            policy={},
        )
        session.add(runtime_space)
        session.flush()
        session.add(
            RuntimeSpaceQuota(
                workspace_id=workspace.id,
                runtime_space_id=runtime_space.id,
                quota_key="active_runs",
                limit_value=1,
            )
        )
        first_step = _seed_task_step(
            session,
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            title="High priority",
            priority=10,
        )
        second_step = _seed_task_step(
            session,
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            title="Second priority",
            priority=9,
        )
        session.commit()
        return SchedulerFixture(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            first_step_id=first_step.id,
            second_step_id=second_step.id,
        )


def _seed_due_scheduled_job(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
) -> tuple[UUID, UUID]:
    with session_factory() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(
            owner=user,
            name="Scheduled Job Workspace",
            slug=f"scheduled-{uuid4()}",
            settings={},
        )
        membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
        session.add_all([user, workspace, membership])
        session.flush()
        scheduled_job = WorkspaceScheduledJob(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="Concurrent scheduled job",
            schedule_type="one_shot",
            schedule_config={"run_at": (now - timedelta(minutes=1)).isoformat()},
            status="active",
            action_type="execute",
            execution_kind="task_plan",
            resource_id=uuid4(),
            parameters={},
            metadata_={},
            next_run_at=now - timedelta(minutes=1),
        )
        session.add(scheduled_job)
        session.commit()
        return workspace.id, scheduled_job.id


def _seed_task_step(
    session: Session,
    *,
    workspace_id: UUID,
    runtime_space_id: UUID,
    title: str,
    priority: int,
) -> TaskStep:
    workspace = session.get(Workspace, workspace_id)
    owner_id = workspace.owner_user_id
    profile_id = test_agent_id(session, workspace_id)
    task = Task(
        workspace_id=workspace_id,
        title=title,
        priority=priority,
        status=TaskStatus.QUEUED.value,
        runtime_space_id=runtime_space_id,
        created_by_user_id=owner_id,
        owner_agent_profile_id=profile_id,
        execution_identity=ExecutionIdentityService(session).capture(workspace_id, owner_id),
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
        assigned_agent_profile_id=profile_id,
        title=f"{title} step",
        status="queued",
        order_index=0,
        runtime_space_id=runtime_space_id,
        dependencies={},
    )
    session.add(step)
    session.flush()
    return step


def _metadata_has_sqlite_json_columns() -> bool:
    return any(
        isinstance(column.type, SqliteJSON)
        for table in Base.metadata.tables.values()
        for column in table.columns
    )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) + chr(34))}"'
