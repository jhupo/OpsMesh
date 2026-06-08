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
from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.identity.models import User, UserAPIToken
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


def test_user_api_tokens_store_hash_and_authenticate_user() -> None:
    session = _session()
    settings = _settings()
    user = User(email="token-owner@example.com", display_name="Token Owner")
    session.add(user)
    session.commit()

    created = AuthorizationService(session).create_user_api_token(
        user_id=user.id,
        name="local cli",
        settings=settings,
    )

    stored = session.get(UserAPIToken, created.record.id)
    assert stored is not None
    assert created.token.startswith("ccut_")
    assert stored.token_hash != created.token
    assert stored.fingerprint.startswith("sha256:")
    assert created.token not in str(stored.__dict__)
    authenticated = AuthorizationService(session).authenticate_user_token(
        created.token,
        settings,
    )
    assert authenticated.user_id == user.id


def test_user_api_token_authentication_rejects_revoked_token() -> None:
    session = _session()
    settings = _settings()
    user = User(email="revoked-token@example.com", display_name="Revoked Token")
    session.add(user)
    session.commit()
    created = AuthorizationService(session).create_user_api_token(
        user_id=user.id,
        name="temporary",
        settings=settings,
    )

    revoked = AuthorizationService(session).revoke_user_api_token(
        user_id=user.id,
        token_id=created.record.id,
    )

    assert revoked is not None
    assert revoked.status == "revoked"
    with pytest.raises(AuthenticationError, match="Invalid or inactive user token"):
        AuthorizationService(session).authenticate_user_token(created.token, settings)


def test_user_api_token_authentication_rejects_disabled_user() -> None:
    session = _session()
    settings = _settings()
    user = User(email="disabled-token-user@example.com", display_name="Disabled")
    session.add(user)
    session.commit()
    created = AuthorizationService(session).create_user_api_token(
        user_id=user.id,
        name="owned by disabled user",
        settings=settings,
    )
    user.status = "disabled"
    session.commit()

    with pytest.raises(AuthenticationError, match="Invalid or inactive user token"):
        AuthorizationService(session).authenticate_user_token(created.token, settings)


def test_register_and_password_login_store_hash_not_plaintext() -> None:
    session = _session()
    settings = _settings()
    service = AuthorizationService(session)

    user = service.register_user(
        email="Password.Owner@Example.COM",
        display_name="Password Owner",
        password="correct horse battery staple",
    )
    created = service.login_with_password(
        email="password.owner@example.com",
        password="correct horse battery staple",
        settings=settings,
    )

    stored = session.get(User, user.id)
    assert stored is not None
    assert stored.email == "password.owner@example.com"
    assert stored.password_hash is not None
    assert stored.password_hash != "correct horse battery staple"
    assert "correct horse battery staple" not in str(stored.__dict__)
    assert created.token.startswith("ccut_")


def test_password_login_rejects_wrong_password() -> None:
    session = _session()
    settings = _settings()
    service = AuthorizationService(session)
    service.register_user(
        email="wrong-password@example.com",
        display_name="Wrong Password",
        password="correct horse battery staple",
    )

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        service.login_with_password(
            email="wrong-password@example.com",
            password="incorrect password",
            settings=settings,
        )


def test_change_password_and_revoke_all_user_api_tokens() -> None:
    session = _session()
    settings = _settings()
    service = AuthorizationService(session)
    user = service.register_user(
        email="change-password@example.com",
        display_name="Change Password",
        password="old password value",
    )
    first = service.login_with_password(
        email=user.email,
        password="old password value",
        settings=settings,
    )
    second = service.create_user_api_token(
        user_id=user.id,
        name="extra token",
        settings=settings,
    )

    service.change_password(
        user_id=user.id,
        current_password="old password value",
        new_password="new password value",
    )
    revoked = service.revoke_all_user_api_tokens(user_id=user.id)

    assert len(revoked) == 2
    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        service.login_with_password(
            email=user.email,
            password="old password value",
            settings=settings,
        )
    service.login_with_password(
        email=user.email,
        password="new password value",
        settings=settings,
    )
    with pytest.raises(AuthenticationError, match="Invalid or inactive user token"):
        service.authenticate_user_token(first.token, settings)
    with pytest.raises(AuthenticationError, match="Invalid or inactive user token"):
        service.authenticate_user_token(second.token, settings)


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


def _settings() -> Settings:
    return Settings(
        environment="test",
        internal_api_token="internal-token",
        token_hash_pepper="test-pepper",
        database_url="sqlite+pysqlite:///:memory:",
    )


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
