import asyncio
import json
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.session_management import PersistentAgentSessionManagementService
from backend.app.agent_runtime.sessions import (
    ACTIVE_SESSION_STATUS,
    ARCHIVED_SESSION_STATUS,
    FROZEN_SESSION_STATUS,
    PersistentAgentSession,
    PersistentAgentSessionItem,
    PersistentAgentSessionRef,
    SQLAlchemyAgentSession,
)
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace


def test_sqlalchemy_agent_session_persists_items_across_instances() -> None:
    session = _session()
    workspace = _add_workspace(session)
    ref = PersistentAgentSessionRef(
        session_key=f"{workspace.id}:team_agent:team-1:agent-1",
        workspace_id=workspace.id,
        scope_type="team_agent",
        scope_id="team-1:agent-1",
    )
    first = SQLAlchemyAgentSession(db_session=session, ref=ref)

    asyncio.run(
        first.add_items(
            [
                {"role": "user", "content": "remember company strategy"},
                {"role": "assistant", "content": "strategy stored"},
            ]
        )
    )

    second = SQLAlchemyAgentSession(db_session=session, ref=ref)
    assert asyncio.run(second.get_items()) == [
        {"role": "user", "content": "remember company strategy"},
        {"role": "assistant", "content": "strategy stored"},
    ]
    assert asyncio.run(second.get_items(limit=1)) == [
        {"role": "assistant", "content": "strategy stored"}
    ]

    stored_session = session.scalar(
        select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.session_key == ref.session_key,
        )
    )
    assert stored_session is not None
    assert stored_session.scope_type == "team_agent"
    assert stored_session.scope_id == "team-1:agent-1"
    assert session.scalar(select(PersistentAgentSessionItem)) is not None


def test_sqlalchemy_agent_session_pop_and_clear() -> None:
    session = _session()
    workspace = _add_workspace(session)
    ref = PersistentAgentSessionRef(
        session_key=f"{workspace.id}:task_agent:task-1:agent-1",
        workspace_id=workspace.id,
        scope_type="task_agent",
        scope_id="task-1:agent-1",
    )
    agent_session = SQLAlchemyAgentSession(db_session=session, ref=ref)
    asyncio.run(
        agent_session.add_items(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ]
        )
    )

    assert asyncio.run(agent_session.pop_item()) == {"role": "assistant", "content": "second"}
    assert asyncio.run(agent_session.get_items()) == [{"role": "user", "content": "first"}]

    asyncio.run(agent_session.clear_session())

    assert asyncio.run(agent_session.get_items()) == []


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
        openai_conversation_id="conv_123",
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
    assert target.openai_conversation_id is None
    cleared = service.get_session(workspace_id=workspace.id, session_id=target.id)
    assert cleared is not None
    assert cleared.session.item_count == 0


def test_session_management_compacts_first_items_and_keeps_recent_items() -> None:
    session = _session()
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="compact",
        items=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"},
            {"role": "user", "content": "five"},
        ],
    )
    service = PersistentAgentSessionManagementService(session)

    result = service.compact_session(
        workspace_id=workspace.id,
        session_id=target.id,
        fold_first_n=3,
        keep_recent_m=2,
        summary_role="system",
    )

    assert result is not None
    assert result.folded_item_count == 3
    assert result.retained_item_count == 2
    detail = service.get_session(workspace_id=workspace.id, session_id=target.id, item_limit=10)
    assert detail is not None
    assert detail.session.item_count == 3
    assert [item.sequence for item in detail.items] == [1, 2, 3]
    assert detail.items[0].item["role"] == "system"
    assert "folded_items: 3" in detail.items[0].item["content"]
    assert [item.item["content"] for item in detail.items[1:]] == ["four", "five"]


def test_session_management_compact_if_needed_threshold_and_retention() -> None:
    session = _session()
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="auto-compact",
        items=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"},
        ],
    )
    service = PersistentAgentSessionManagementService(session)

    result = service.compact_if_needed(
        workspace_id=workspace.id,
        session_id=target.id,
        policy={
            "auto_compact_enabled": True,
            "session_max_items": 3,
            "session_keep_recent_items": 1,
            "summary_role": "system",
        },
    )

    assert result is not None
    assert result.folded_item_count == 3
    assert result.retained_item_count == 1
    detail = service.get_session(workspace_id=workspace.id, session_id=target.id, item_limit=10)
    assert detail is not None
    assert detail.session.item_count == 2
    assert [item.sequence for item in detail.items] == [1, 2]
    assert detail.items[0].item["role"] == "system"
    assert detail.items[1].item["content"] == "four"


def test_session_management_compact_if_needed_skips_below_threshold() -> None:
    session = _session()
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="small-session",
        items=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
    )
    service = PersistentAgentSessionManagementService(session)

    result = service.compact_if_needed(
        workspace_id=workspace.id,
        session_id=target.id,
        policy={
            "auto_compact_enabled": True,
            "session_max_items": 2,
            "session_keep_recent_items": 1,
        },
    )

    assert result is None
    detail = service.get_session(workspace_id=workspace.id, session_id=target.id, item_limit=10)
    assert detail is not None
    assert [item.item["content"] for item in detail.items] == ["one", "two"]


def test_session_management_compact_if_needed_leaves_frozen_session_unchanged() -> None:
    session = _session()
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="frozen-session",
        items=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ],
    )
    target.status = FROZEN_SESSION_STATUS
    session.flush()
    service = PersistentAgentSessionManagementService(session)

    result = service.compact_if_needed(
        workspace_id=workspace.id,
        session_id=target.id,
        policy={
            "auto_compact_enabled": True,
            "session_max_items": 1,
            "session_keep_recent_items": 0,
        },
    )

    assert result is None
    detail = service.get_session(workspace_id=workspace.id, session_id=target.id, item_limit=10)
    assert detail is not None
    assert [item.item["content"] for item in detail.items] == ["one", "two", "three"]


def test_session_management_rejects_overlapping_compaction_window() -> None:
    session = _session()
    workspace = _add_workspace(session)
    target = _add_persistent_session(
        session,
        workspace_id=workspace.id,
        session_key="bad-compact",
        items=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
    )
    service = PersistentAgentSessionManagementService(session)

    try:
        service.compact_session(
            workspace_id=workspace.id,
            session_id=target.id,
            fold_first_n=2,
            keep_recent_m=1,
        )
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("expected overlapping compaction window to fail")


def test_sqlalchemy_agent_session_does_not_mutate_archived_or_frozen_sessions() -> None:
    session = _session()
    workspace = _add_workspace(session)
    ref = PersistentAgentSessionRef(
        session_key=f"{workspace.id}:team_agent:team-1:agent-1",
        workspace_id=workspace.id,
        scope_type="team_agent",
        scope_id="team-1:agent-1",
    )
    agent_session = SQLAlchemyAgentSession(db_session=session, ref=ref)
    asyncio.run(agent_session.add_items([{"role": "user", "content": "first"}]))
    stored_session = session.scalar(
        select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace.id,
            PersistentAgentSession.session_key == ref.session_key,
        )
    )
    assert stored_session is not None

    stored_session.status = FROZEN_SESSION_STATUS
    session.flush()
    asyncio.run(agent_session.add_items([{"role": "assistant", "content": "ignored"}]))
    assert asyncio.run(agent_session.pop_item()) is None
    stored_session.status = ARCHIVED_SESSION_STATUS
    session.flush()

    assert asyncio.run(agent_session.get_items()) == []
    assert (
        session.scalar(
            select(func.count(PersistentAgentSessionItem.id)).where(
                PersistentAgentSessionItem.persistent_session_id == stored_session.id
            )
        )
        == 1
    )


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
    openai_conversation_id: str | None = None,
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
        openai_conversation_id=openai_conversation_id,
        session_metadata={"source": "test"},
    )
    session.add(persistent_session)
    session.flush()
    for sequence, item in enumerate(items or [], start=1):
        session.add(
            PersistentAgentSessionItem(
                workspace_id=workspace_id,
                persistent_session_id=persistent_session.id,
                sequence=sequence,
                item=item,
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
