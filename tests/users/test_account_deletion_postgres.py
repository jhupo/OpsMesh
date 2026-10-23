"""Credential identity is independent of the account whose deletion revokes it."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from opsmesh.platform.audit.contracts import AuditActor
from opsmesh.users.auth.models import LoginSession, UserCredential
from opsmesh.users.service import create_user, delete_account


def test_account_deletion_removes_credential_by_user_foreign_key(database_sessions):
    with database_sessions.begin() as session:
        user = create_user(session, email="delete@example.com", display_name="Delete")
        credential = UserCredential(user_id=user.id, password_hash="stored-verifier")
        login = LoginSession(
            user_id=user.id,
            token_hash=uuid4().hex * 2,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add_all([credential, login])
        session.flush()
        assert credential.id != user.id
        user_id, credential_id, login_id = user.id, credential.id, login.id
    with database_sessions.begin() as session:
        deleted = delete_account(session, user_id=user_id, actor=AuditActor(actor_type="platform"))
        assert deleted.status == "deleted"
    with database_sessions() as session:
        assert session.get(UserCredential, credential_id) is None
        assert session.get(LoginSession, login_id).revoked_at is not None
