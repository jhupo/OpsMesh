import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.sessions.models import (
    ACTIVE_SESSION_STATUS,
    ARCHIVED_SESSION_STATUS,
    FROZEN_SESSION_STATUS,
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionRecord,
)
from opsmesh.agents.sessions.service import AgentSessionService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.tasks.models import Task
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace

pytest_plugins = ["tests.fixtures.agent_database"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_session_management_lists_filters_and_paginates_workspace_sessions(agent_database) -> None:
    session = agent_database
    workspace = _add_workspace(session)
    other_workspace = _add_workspace(session)
    agent = AgentProfile(workspace_id=workspace.id, name="Reader", role="expert")
    other_agent = AgentProfile(workspace_id=workspace.id, name="Writer", role="expert")
    team = AgentTeam(workspace_id=workspace.id, name="Research")
    session.add_all([agent, other_agent, team])
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=workspace.owner_user_id,
        agent_team_id=team.id,
        title="Read history",
    )
    session.add(task)
    session.flush()
    agent_id, team_id, task_id = agent.id, team.id, task.id
    matching = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="matching",
        agent_profile_id=agent_id,
        agent_team_id=team_id,
        task_id=task_id,
        items=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ],
    )
    _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="other-agent",
        agent_profile_id=other_agent.id,
        items=[{"role": "user", "content": "other"}],
    )
    _add_persistent_session(
        session,
        workspace_id=other_workspace.id,
        session_key="foreign",
        items=[{"role": "user", "content": "foreign"}],
    )

    service = AgentSessionService(session)

    summaries = service.list_sessions(
        workspace_id=workspace.id,
        agent_profile_id=agent_id,
        agent_team_id=team_id,
        task_id=task_id,
        status=ACTIVE_SESSION_STATUS,
    )
    assert [item.id for item in summaries] == [matching.id]
    assert summaries[0].item_count == 2
    assert summaries[0].latest_item_metadata is not None
    assert summaries[0].latest_item_metadata["content_preview"] == "second"

    detail = service.get_session(
        workspace_id=workspace.id,
        session_id=matching.id,
        item_limit=1,
        item_offset=1,
    )
    assert detail is not None
    assert detail.session.id == matching.id
    assert [item.item["content"] for item in detail.items] == ["second"]


def test_session_management_status_clear_and_workspace_scope(agent_database) -> None:
    session = agent_database
    workspace = _add_workspace(session)
    other_workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="target",
        items=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ],
    )
    service = AgentSessionService(session)

    assert service.freeze_session(workspace_id=other_workspace.id, session_id=target.id) is None

    frozen = service.freeze_session(workspace_id=workspace.id, session_id=target.id)
    assert frozen is not None
    assert frozen.status == FROZEN_SESSION_STATUS

    archived = service.archive_session(workspace_id=workspace.id, session_id=target.id)
    assert archived is not None
    assert archived.status == ARCHIVED_SESSION_STATUS

    active = service.activate_session(workspace_id=workspace.id, session_id=target.id)
    assert active is not None
    assert active.status == ACTIVE_SESSION_STATUS

    deleted = service.clear_session_items(workspace_id=workspace.id, session_id=target.id)
    assert deleted == 2
    session.refresh(target)
    cleared = service.get_session(workspace_id=workspace.id, session_id=target.id)
    assert cleared is not None
    assert cleared.session.item_count == 0


def test_session_clear_waits_for_inflight_history_without_locking_message_rows(agent_database):
    session = agent_database
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="concurrent-clear",
        items=[{"role": "user", "content": "inflight history"}],
    )
    workspace_id, target_id = workspace.id, target.id
    session.commit()
    sessions = sessionmaker(session.get_bind(), expire_on_commit=False)
    started = Event()
    backend_pid: list[int] = []

    def clear_history():
        with sessions() as writer:
            backend_pid.append(writer.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            count = AgentSessionService(writer).clear_session_items(
                workspace_id=workspace_id, session_id=target_id
            )
            writer.commit()
            return count

    with ThreadPoolExecutor(max_workers=1) as executor, sessions() as inflight:
        inflight.scalar(
            select(AgentSessionMetadata)
            .where(AgentSessionMetadata.id == target_id)
            .with_for_update()
        )
        pending = executor.submit(clear_history)
        try:
            assert started.wait(10)
            deadline = monotonic() + 10
            with sessions() as observer:
                while True:
                    wait_type = observer.scalar(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                        {"pid": backend_pid[0]},
                    )
                    observer.rollback()
                    if wait_type == "Lock":
                        break
                    assert monotonic() < deadline, "Session clear did not wait for the writer"
                    sleep(0.02)
                rows = observer.scalars(
                    select(SDKSessionMessageRecord)
                    .where(SDKSessionMessageRecord.session_id == "concurrent-clear")
                    .with_for_update(nowait=True)
                ).all()
                assert len(rows) == 1
        finally:
            inflight.commit()
        assert pending.result(timeout=10) == 1


def _add_workspace(session: Session) -> Workspace:
    user = User(email=f"owner-{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme AI Company", slug=f"acme-{uuid4()}", settings={})
    session.add(workspace)
    session.flush()
    return workspace


def _add_persistent_session(
    session: Session,
    *,
    workspace_id,
    session_key: str,
    agent_profile_id=None,
    agent_team_id=None,
    task_id=None,
    items: list[dict[str, object]] | None = None,
) -> AgentSessionMetadata:
    persistent_session = AgentSessionMetadata(
        workspace_id=workspace_id,
        session_key=session_key,
        scope_type="team_agent",
        scope_id=session_key,
        agent_profile_id=agent_profile_id,
        agent_team_id=agent_team_id,
        task_id=task_id,
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    session.add(SDKSessionRecord(session_id=persistent_session.session_key))
    session.flush()
    for item in items or []:
        session.add(
            SDKSessionMessageRecord(
                session_id=persistent_session.session_key,
                message_data=json.dumps(item),
            )
        )
    session.flush()
    return persistent_session
