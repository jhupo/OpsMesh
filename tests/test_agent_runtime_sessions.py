import json
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.sessions.management import PersistentAgentSessionManagementService
from opsmesh.agents.sessions.models import (
    ACTIVE_SESSION_STATUS,
    ARCHIVED_SESSION_STATUS,
    FROZEN_SESSION_STATUS,
    PersistentAgentSession,
    SDKAgentMessage,
    SDKAgentSession,
)
from opsmesh.identity.users.models import User
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace


def test_session_management_lists_filters_and_paginates_workspace_sessions() -> None:
    session = _session()
    workspace = _add_workspace(session)
    other_workspace = _add_workspace(session)
    agent_id = uuid4()
    team_id = uuid4()
    task_id = uuid4()
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
        agent_profile_id=uuid4(),
        items=[{"role": "user", "content": "other"}],
    )
    _add_persistent_session(
        session,
        workspace_id=other_workspace.id,
        session_key="foreign",
        agent_profile_id=agent_id,
        agent_team_id=team_id,
        task_id=task_id,
        items=[{"role": "user", "content": "foreign"}],
    )

    service = PersistentAgentSessionManagementService(session)

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


def test_session_management_status_clear_and_workspace_scope() -> None:
    session = _session()
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
    service = PersistentAgentSessionManagementService(session)

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


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        json_serializer=json.dumps,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


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
) -> PersistentAgentSession:
    persistent_session = PersistentAgentSession(
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
    session.add(SDKAgentSession(session_id=persistent_session.session_key))
    session.flush()
    for item in items or []:
        session.add(
            SDKAgentMessage(
                session_id=persistent_session.session_key,
                message_data=json.dumps(item),
            )
        )
    session.flush()
    return persistent_session


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
