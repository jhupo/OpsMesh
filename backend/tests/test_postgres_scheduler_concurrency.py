import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import Workspace, WorkspaceMember

POSTGRES_TEST_URL_ENV = "CHAINCLOUD_TEST_POSTGRES_URL"

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

    with _temporary_postgres_schema() as engine:
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


class _temporary_postgres_schema:
    def __init__(self) -> None:
        self._schema = f"cc_test_{uuid4().hex}"
        self._admin_engine: Engine | None = None
        self._schema_engine: Engine | None = None

    def __enter__(self) -> Engine:
        database_url = os.environ[POSTGRES_TEST_URL_ENV]
        quoted_schema = _quote_identifier(self._schema)
        self._admin_engine = create_engine(database_url, future=True)
        with self._admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        self._schema_engine = create_engine(
            database_url,
            future=True,
            pool_size=4,
            max_overflow=0,
            connect_args={"options": f"-csearch_path={self._schema}"},
        )
        return self._schema_engine

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        quoted_schema = _quote_identifier(self._schema)
        if self._admin_engine is not None:
            with self._admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        if self._schema_engine is not None:
            self._schema_engine.dispose()
        if self._admin_engine is not None:
            self._admin_engine.dispose()


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
        runtime_space = RuntimeSpace(
            workspace_id=workspace.id,
            name="Team runtime space",
            scope="team",
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


def _seed_task_step(
    session: Session,
    *,
    workspace_id: UUID,
    runtime_space_id: UUID,
    title: str,
    priority: int,
) -> TaskStep:
    task = Task(
        workspace_id=workspace_id,
        title=title,
        priority=priority,
        status=TaskStatus.QUEUED.value,
        runtime_space_id=runtime_space_id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace_id,
        task_id=task.id,
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
