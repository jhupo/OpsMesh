from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import AgentRuntimeResumeState
from backend.app.agent_runtime.state_store import AgentRunStateStore
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_agent_run_state_store_encrypts_updates_and_consumes_state() -> None:
    session = _session()
    workspace, run = _seed_run(session)
    store = AgentRunStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )
    first_state = AgentRuntimeResumeState(
        provider="openai_agents",
        serialized_state='{"$schemaVersion":"1.10","secret":"private-value"}',
        schema_version="1.10",
        sdk_version="0.17.2",
    )

    first = store.save(workspace_id=workspace.id, run_id=run.id, state=first_state)
    assert "private-value" not in first.encrypted_state
    assert store.load(workspace_id=workspace.id, run_id=run.id) == first_state

    second_state = AgentRuntimeResumeState(
        provider="openai_agents",
        serialized_state='{"$schemaVersion":"1.10","current_turn":2}',
        schema_version="1.10",
        sdk_version="0.17.2",
    )
    second = store.save(workspace_id=workspace.id, run_id=run.id, state=second_state)

    assert second.id == first.id
    assert second.revision == 2
    assert store.load(workspace_id=workspace.id, run_id=run.id) == second_state

    store.mark_consumed(workspace_id=workspace.id, run_id=run.id)
    assert store.load(workspace_id=workspace.id, run_id=run.id) is None


def test_agent_run_state_store_is_workspace_scoped() -> None:
    session = _session()
    workspace, run = _seed_run(session)
    store = AgentRunStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )
    store.save(
        workspace_id=workspace.id,
        run_id=run.id,
        state=AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
        ),
    )

    assert store.load(workspace_id=uuid4(), run_id=run.id) is None


def test_agent_run_state_store_rejects_oversized_state() -> None:
    session = _session()
    workspace, run = _seed_run(session)
    store = AgentRunStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )

    with pytest.raises(ValueError, match="allowed size"):
        store.save(
            workspace_id=workspace.id,
            run_id=run.id,
            state=AgentRuntimeResumeState(
                provider="openai_agents",
                serialized_state="x" * (8 * 1024 * 1024 + 1),
            ),
        )


def _seed_run(session: Session) -> tuple[Workspace, AgentRun]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.flush()
    run = AgentRun(workspace_id=workspace.id, status="running")
    session.add(run)
    session.commit()
    return workspace, run


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
