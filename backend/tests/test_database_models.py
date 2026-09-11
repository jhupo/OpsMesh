import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.operations.models import WorkerLease
from backend.app.runtime_manager.spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import TaskEventOutbox
from backend.app.workspaces.models import Workspace, WorkspaceMember, WorkspaceQuota


def test_workspace_membership_round_trip() -> None:
    engine = _sqlite_engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        user = User(email="owner@example.com", display_name="Owner")
        workspace = Workspace(owner=user, name="Acme AI Company", slug="acme", settings={})
        membership = WorkspaceMember(workspace=workspace, user=user, role="owner")

        session.add_all([user, workspace, membership])
        session.commit()

        stored = session.scalar(
            select(WorkspaceMember)
            .join(Workspace)
            .join(User)
            .where(Workspace.slug == "acme", User.email == "owner@example.com")
        )

    assert stored is not None
    assert stored.role == "owner"
    assert stored.workspace_id == workspace.id
    assert stored.user_id == user.id


def test_workspace_owned_tables_have_workspace_id() -> None:
    workspace_owned_tables = [WorkspaceMember.__table__]

    for table in workspace_owned_tables:
        assert "workspace_id" in table.c


def test_sqlite_can_create_registered_model_tables() -> None:
    engine = _sqlite_engine()

    assert "worker_leases" in Base.metadata.tables
    assert "workspace_quotas" in Base.metadata.tables
    assert "runtime_space_quotas" in Base.metadata.tables
    assert inspect(engine).has_table("worker_leases")


def test_postgresql_can_compile_key_constraint_tables() -> None:
    dialect = postgresql.dialect()
    worker_lease_ddl = str(CreateTable(WorkerLease.__table__).compile(dialect=dialect))
    task_outbox_ddl = str(CreateTable(TaskEventOutbox.__table__).compile(dialect=dialect))
    workspace_quota_ddl = str(CreateTable(WorkspaceQuota.__table__).compile(dialect=dialect))
    runtime_quota_ddl = str(CreateTable(RuntimeSpaceQuota.__table__).compile(dialect=dialect))

    assert "ck_worker_leases_status_valid" in worker_lease_ddl
    assert "ck_worker_leases_attempt_non_negative" in worker_lease_ddl
    assert "ck_task_event_outbox_status_valid" in task_outbox_ddl
    assert "ck_task_event_outbox_attempts_non_negative" in task_outbox_ddl
    assert "uq_task_event_outbox_event_id" in task_outbox_ddl
    assert "ck_workspace_quotas_limit_value_non_negative" in workspace_quota_ddl
    assert "ck_workspace_quotas_reserved_value_non_negative" in workspace_quota_ddl
    assert "ck_runtime_space_quotas_limit_value_non_negative" in runtime_quota_ddl
    assert "ck_runtime_space_quotas_reserved_value_non_negative" in runtime_quota_ddl


def test_worker_lease_constraints_reject_invalid_status_and_attempt() -> None:
    engine = _sqlite_engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        workspace = _add_workspace(session)
        session.commit()

        base_values = {
            "workspace_id": workspace.id,
            "worker_id": "worker-constraints",
            "queue_name": "agent_runs",
            "job_id": uuid4(),
            "job_type": "agent.run",
            "resource_id": uuid4(),
            "status": "running",
            "attempt": 0,
            "metadata": {},
            "started_at": datetime.now(UTC),
        }

        with pytest.raises(IntegrityError):
            session.execute(
                WorkerLease.__table__.insert().values(
                    {
                        **base_values,
                        "job_id": uuid4(),
                        "status": "unknown",
                    }
                )
            )
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                WorkerLease.__table__.insert().values(
                    {
                        **base_values,
                        "job_id": uuid4(),
                        "attempt": -1,
                    }
                )
            )
            session.commit()


def test_quota_constraints_reject_negative_values() -> None:
    engine = _sqlite_engine()
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        workspace = _add_workspace(session)
        runtime_space = _add_runtime_space(session, workspace)
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                WorkspaceQuota.__table__.insert().values(
                    workspace_id=workspace.id,
                    quota_key="negative_limit",
                    limit_value=-1,
                    reserved_value=0,
                    unit="count",
                    status="active",
                )
            )
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.execute(
                RuntimeSpaceQuota.__table__.insert().values(
                    workspace_id=workspace.id,
                    runtime_space_id=runtime_space.id,
                    quota_key="negative_reserved",
                    limit_value=1,
                    reserved_value=-1,
                    unit="count",
                    status="active",
                )
            )
            session.commit()


def _sqlite_engine():
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        json_serializer=json.dumps,
    )
    Base.metadata.create_all(engine)
    return engine


def _add_workspace(session):
    user = User(email=f"owner-{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme AI Company", slug=f"acme-{uuid4()}", settings={})
    session.add(workspace)
    session.flush()
    return workspace


def _add_runtime_space(session, workspace):
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        name="Default",
        scope="workspace",
        status="active",
        policy={},
        network_policy={},
        storage_policy={},
        cleanup_policy={},
    )
    session.add(runtime_space)
    session.flush()
    return runtime_space


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
