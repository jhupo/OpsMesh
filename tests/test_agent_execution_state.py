import os
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentResumeState
from opsmesh.agents.execution.state import MAX_SERIALIZED_RUN_STATE_BYTES, AgentResumeStateStore
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember

pytest_plugins = ["tests.fixtures.agent_database"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_agent_run_state_store_encrypts_updates_and_consumes_state(agent_database) -> None:
    session = agent_database
    workspace, run = _seed_run(session)
    store = AgentResumeStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )
    first_state = AgentResumeState(
        provider="openai_agents",
        serialized_state='{"$schemaVersion":"1.10","secret":"private-value"}',
        schema_version="1.10",
        sdk_version="0.17.2",
    )

    first = store.save(workspace_id=workspace.id, run_id=run.id, state=first_state)
    assert "private-value" not in first.encrypted_state
    assert store.load(workspace_id=workspace.id, run_id=run.id) == first_state

    second_state = AgentResumeState(
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


def test_agent_run_state_store_is_workspace_scoped(agent_database) -> None:
    session = agent_database
    workspace, run = _seed_run(session)
    store = AgentResumeStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )
    store.save(
        workspace_id=workspace.id,
        run_id=run.id,
        state=AgentResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
        ),
    )

    assert store.load(workspace_id=uuid4(), run_id=run.id) is None


def test_agent_run_state_store_rejects_oversized_state(agent_database) -> None:
    session = agent_database
    workspace, run = _seed_run(session)
    store = AgentResumeStateStore(
        session,
        SecretEncryptionService(secret="run-state-secret", key_id="test-key"),
    )

    with pytest.raises(ValueError, match="allowed size"):
        store.save(
            workspace_id=workspace.id,
            run_id=run.id,
            state=AgentResumeState(
                provider="openai_agents",
                serialized_state="x" * (MAX_SERIALIZED_RUN_STATE_BYTES + 1),
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
