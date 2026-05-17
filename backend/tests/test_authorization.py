from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.auth.errors import AuthenticationError, PermissionDeniedError
from backend.app.auth.permissions import WorkspaceAction, role_allows
from backend.app.auth.service import AuthorizationService
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_role_permissions_are_hierarchical() -> None:
    assert role_allows("owner", WorkspaceAction.OWNER)
    assert role_allows("admin", WorkspaceAction.ADMIN)
    assert role_allows("operator", WorkspaceAction.WRITE)
    assert role_allows("operator", WorkspaceAction.APPROVE)
    assert role_allows("operator", WorkspaceAction.OPERATE)
    assert role_allows("operator", WorkspaceAction.MANAGE_RUNTIME)
    assert not role_allows("operator", WorkspaceAction.MANAGE_CAPABILITY)
    assert not role_allows("operator", WorkspaceAction.MANAGE_MEMBERS)
    assert role_allows("viewer", WorkspaceAction.READ)
    assert not role_allows("viewer", WorkspaceAction.WRITE)
    assert not role_allows("viewer", WorkspaceAction.APPROVE)
    assert not role_allows("unknown", WorkspaceAction.READ)


def test_require_workspace_allows_active_member() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, role="operator")

    context = AuthorizationService(session).require_workspace(
        user_id=user.id,
        workspace_id=workspace.id,
        action=WorkspaceAction.WRITE,
    )

    assert context.user.user_id == user.id
    assert context.workspace.id == workspace.id
    assert context.membership.role == "operator"


def test_require_workspace_rejects_cross_workspace_access() -> None:
    session = _session()
    user, _ = _seed_workspace(session, role="owner")
    _, other_workspace = _seed_workspace(
        session,
        role="owner",
        email="other@example.com",
        slug="other",
    )

    with pytest.raises(PermissionDeniedError, match="not an active member"):
        AuthorizationService(session).require_workspace(
            user_id=user.id,
            workspace_id=other_workspace.id,
            action=WorkspaceAction.READ,
        )


def test_require_workspace_rejects_insufficient_role() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, role="viewer")

    with pytest.raises(PermissionDeniedError, match="does not allow"):
        AuthorizationService(session).require_workspace(
            user_id=user.id,
            workspace_id=workspace.id,
            action=WorkspaceAction.WRITE,
        )


def test_authenticate_user_rejects_inactive_user() -> None:
    session = _session()
    user = User(email="inactive@example.com", display_name="Inactive", status="disabled")
    session.add(user)
    session.commit()

    with pytest.raises(AuthenticationError):
        AuthorizationService(session).authenticate_user(user.id)


def test_worker_resource_workspace_mismatch_is_rejected() -> None:
    session = _session()
    service = AuthorizationService(session)

    with pytest.raises(PermissionDeniedError, match="Resource does not belong"):
        service.ensure_resource_workspace(
            resource_workspace_id=uuid4(),
            expected_workspace_id=uuid4(),
        )


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    role: str,
    email: str = "owner@example.com",
    slug: str = "workspace",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
