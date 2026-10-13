import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.bootstrap.models import register_models
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.platform.operations.history import PlatformHistoryService
from opsmesh.platform.operations.models import AdminOperationRecord, PlatformMetricSnapshot
from opsmesh.platform.operations.service import AdminOperationService
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import ConflictError
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


@pytest.mark.parametrize("same_workspace", [True, False])
def test_concurrent_admin_submission_has_one_intent_and_one_audit(same_workspace):
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Admin", platform_admin=True)
            workspaces = [Workspace(owner=user, name="Test", slug=uuid4().hex) for _ in range(2)]
            session.add_all([user, *workspaces])
            session.commit()
            actor = AuthenticatedUser.from_model(user)
            workspace_ids = [workspaces[0].id, workspaces[0 if same_workspace else 1].id]
        barrier = Barrier(2)
        request_id = uuid4()

        def submit(workspace_id):
            with factory() as session:
                barrier.wait(timeout=10)
                try:
                    row = AdminOperationService(session).submit(
                        request_id=request_id,
                        workspace_id=workspace_id,
                        actor=actor,
                        operation="verify_audit",
                        parameters={"reason": "Concurrent request"},
                    )
                    session.commit()
                    return str(row.id)
                except ConflictError:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, identifier) for identifier in workspace_ids]
            results = [future.result(timeout=20) for future in futures]
        assert results.count("conflict") == (0 if same_workspace else 1)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(AdminOperationRecord)) == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "platform.operation.requested")
                )
                == 1
            )


def test_parallel_history_sampling_commits_only_one_complete_snapshot():
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(bind=engine)
        barrier = Barrier(2)
        now = datetime.now(UTC)

        def capture():
            with factory() as session:
                barrier.wait(timeout=10)
                PlatformHistoryService(session).capture(Settings(environment="test"), now=now)
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(capture) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        with factory() as session:
            rows = session.scalars(select(PlatformMetricSnapshot)).all()
            assert len(rows) == 1
            assert set(rows[0].values) == {"runs", "temporal_dispatch", "runtimes", "approvals"}


def test_explicit_admin_cancellation_stages_only_target_workspace():
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(UTC)
        with factory() as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Owner")
            workspace = Workspace(owner=user, name="Recovery", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.flush()
            agent = AgentProfile(workspace_id=workspace.id, name="Agent", role="operator")
            session.add(agent)
            session.flush()
            stale = AgentRun(
                workspace_id=workspace.id,
                agent_profile_id=agent.id,
                model="test",
                input={},
                status="running",
                started_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
            )
            session.add(stale)
            session.commit()
            workspace_id, stale_id = workspace.id, stale.id
        from opsmesh.orchestration.runs.control import RunControlService
        from opsmesh.runtime.temporal.models import TemporalOutboxEntry

        with factory() as session:
            assert (
                RunControlService(session).cancel_run(
                    workspace_id=uuid4(), run_id=stale_id, actor_user_id=user.id
                )
                is None
            )
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 0
            RunControlService(session).cancel_run(
                workspace_id=workspace_id, run_id=stale_id, actor_user_id=user.id
            )
            assert session.get(AgentRun, stale_id).status == "cancelled"
            commands = session.scalars(select(TemporalOutboxEntry)).all()
            assert len(commands) == 1
            assert commands[0].workspace_id == workspace_id
