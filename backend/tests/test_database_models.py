import json

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import sessionmaker

from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_workspace_membership_round_trip() -> None:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        json_serializer=json.dumps,
    )
    Base.metadata.create_all(engine)
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


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
