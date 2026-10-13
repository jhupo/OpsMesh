"""Real PostgreSQL regression; uses an isolated, disposable schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from opsmesh.bootstrap.models import register_models
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditService
from opsmesh.workspaces.management.models import Workspace


def test_concurrent_audit_writers_with_existing_foreign_key_locks() -> None:
    url = os.environ.get("OPSMESH_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OPSMESH_TEST_POSTGRES_URL for the PostgreSQL lock regression")
    register_models()
    schema = "audit_lock_test_" + uuid4().hex
    engine = create_engine(url)
    isolated = engine.execution_options(schema_translate_map={None: schema})
    metadata = MetaData()
    for name in ("workspaces", "users", "agent_runs"):
        Table(name, metadata, Column("id", UUID(as_uuid=True), primary_key=True))
    AuditEvent.__table__.to_metadata(metadata)
    workspace_id = uuid4()
    barrier = Barrier(2)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        metadata.create_all(isolated)
        with isolated.begin() as connection:
            connection.execute(metadata.tables["workspaces"].insert(), {"id": workspace_id})

        def append_audit(index: int) -> None:
            with Session(isolated) as session:
                # INSERTs referencing a workspace hold this same KEY SHARE lock.
                session.scalar(
                    select(Workspace.id)
                    .where(Workspace.id == workspace_id)
                    .with_for_update(read=True, key_share=True)
                )
                barrier.wait(timeout=10)
                AuditService(session).record_system_action(
                    workspace_id=workspace_id,
                    action="test.concurrent_audit",
                    target_type="workspace",
                    target_id=workspace_id,
                    metadata={"writer": index},
                )
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(append_audit, index) for index in range(2)]
            for future in futures:
                future.result(timeout=20)
        with Session(isolated) as session:
            verification = AuditService(session).verify_workspace_hash_chain(workspace_id)
            assert verification.checked_events == 2
            assert verification.valid
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        engine.dispose()


def test_activity_database_failure_rolls_back_before_following_business_transaction() -> None:
    from sqlalchemy.orm import sessionmaker

    from opsmesh.identity.users.models import User
    from opsmesh.runtime.temporal.activities import ActivityDependencies
    from opsmesh.shared.config import Settings
    from tests.fixtures.database import product_session

    session = product_session()
    services = ActivityDependencies(
        sessions=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        settings=Settings(environment="test"),
    )
    workspace_id = uuid4()
    with pytest.raises(ProgrammingError), services.transaction() as transaction:
        transaction.add(
            Workspace(
                id=workspace_id,
                name="Rolled back",
                slug=uuid4().hex,
                owner=User(email=f"{uuid4()}@test.invalid", display_name="Owner"),
            )
        )
        transaction.flush()
        transaction.execute(text("SELECT * FROM missing_test_table_" + uuid4().hex))
    with services.transaction() as transaction:
        assert transaction.get(Workspace, workspace_id) is None
        assert transaction.scalar(text("SELECT 1")) == 1
