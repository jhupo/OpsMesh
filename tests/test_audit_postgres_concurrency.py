"""Real PostgreSQL regression; uses an isolated, disposable schema."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
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
from opsmesh.orchestration.requests.run_gateway import ModelRunGateway
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


def test_database_failure_is_rolled_back_before_metering_and_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ.get("OPSMESH_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set OPSMESH_TEST_POSTGRES_URL for the PostgreSQL recovery regression")
    engine = create_engine(url)
    with Session(engine) as session:
        errors: list[ProgrammingError] = []
        calls: list[str] = []

        class FailingToolRunner:
            async def run(self, request: object) -> None:
                calls.append("runner")
                try:
                    session.execute(text("SELECT * FROM missing_test_table_" + uuid4().hex))
                except ProgrammingError as exc:
                    errors.append(exc)
                    raise

        costs = Mock(unsafe=True)

        def meter_failure(**values: object) -> None:
            assert session.scalar(text("SELECT 1")) == 1
            assert values["error"] is errors[0]
            calls.append("metered")

        costs.record_attempt.side_effect = meter_failure
        monkeypatch.setattr(
            "opsmesh.orchestration.requests.run_gateway.CostAccountingService",
            lambda _: costs,
        )
        routing = Mock()
        approvals = Mock()
        approvals.requires_approval.return_value = False

        class Gateway(ModelRunGateway):
            def routing(self):
                return routing

            def audit(self):
                return Mock()

            def approvals(self):
                return approvals

        def mark_failed(run: object, error: Exception) -> None:
            assert session.scalar(text("SELECT 1")) == 1
            assert error is errors[0]
            calls.append("failed")

        gateway = Gateway(
            session=session,
            settings=None,
            agent_runner=FailingToolRunner(),
            request_builder=Mock(),
            events=Mock(),
            mark_run_failed=mark_failed,
        )
        run = Mock(workspace_id=uuid4(), id=uuid4())
        request = Mock(provider="openai", model="test-no-inference")
        with pytest.raises(ProgrammingError) as captured:
            asyncio.run(gateway.run_with_provider_fallback(run, request, Mock(attempt=0)))
        assert captured.value is errors[0]
        assert calls == ["runner", "metered", "failed"]
        routing.fallback_request.assert_not_called()
    engine.dispose()
