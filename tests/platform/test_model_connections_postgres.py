"""Current upstream grants, key rotation and admission on real PostgreSQL."""

from uuid import uuid4

import pytest

from opsmesh.identity.users import service as users
from opsmesh.platform.model_connections import service
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.encryption import SecretCipher


@pytest.fixture
def model_accounts(database_sessions):
    with database_sessions.begin() as session:
        admin = users.identity_of(
            users.create_user(
                session,
                email=f"{uuid4()}@example.test",
                display_name="Admin",
                platform_roles=frozenset({"admin"}),
            )
        )
        member = users.identity_of(
            users.create_user(
                session,
                email=f"{uuid4()}@example.test",
                display_name="Member",
            )
        )
    return admin, member


def cipher():
    return SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")


def test_model_grant_is_required_and_revocation_blocks_existing_selection(
    database_sessions, model_accounts
):
    admin, member = model_accounts
    with database_sessions.begin() as session:
        connection = service.create_connection(
            session,
            actor=admin,
            cipher=cipher(),
            name="upstream",
            api_key="test-secret",
            base_url="http://127.0.0.1:8000/v1",
            default_model="m1",
            models=["m1", "m2"],
        )
        identifier = connection.id
        assert "test-secret" not in connection.encrypted_key
    with database_sessions() as session, pytest.raises(ForbiddenError):
        service.resolve_connection(session, actor=member, connection_id=identifier)
    with database_sessions() as session, pytest.raises(ForbiddenError):
        service.resolve_connection(session, actor=admin, connection_id=identifier)
    with database_sessions.begin() as session:
        service.grant_connection(
            session,
            actor=admin,
            connection_id=identifier,
            user_id=member.user_id,
            models=["m1"],
            is_default=True,
        )
        selected = service.resolve_connection(session, actor=member, connection_id=identifier)
        assert selected.model == "m1"
        assert not hasattr(selected, "encrypted_key")
        assert service.resolve_model_secret(
            session, cipher=cipher(), actor=member, connection_id=identifier
        ) == {"api_key": "test-secret"}
    with database_sessions() as session, pytest.raises(ForbiddenError):
        service.resolve_connection(session, actor=member, connection_id=identifier, model="m2")
    with database_sessions.begin() as session:
        service.revoke_connection(
            session, actor=admin, connection_id=identifier, user_id=member.user_id
        )
    with database_sessions() as session, pytest.raises(ForbiddenError):
        service.resolve_model_secret(
            session, cipher=cipher(), actor=member, connection_id=identifier
        )


def test_model_rotation_revision_and_disabled_connection_are_enforced(
    database_sessions, model_accounts
):
    admin, member = model_accounts
    with database_sessions.begin() as session:
        connection = service.create_connection(
            session,
            actor=admin,
            cipher=cipher(),
            name="rotated",
            api_key="old-key",
            base_url="https://example.test/v1",
            default_model="m1",
            models=["m1"],
        )
        identifier = connection.id
        service.grant_connection(
            session, actor=admin, connection_id=identifier, user_id=member.user_id, models=["m1"]
        )
        service.update_connection(
            session,
            actor=admin,
            cipher=cipher(),
            connection_id=identifier,
            expected_revision=1,
            api_key="new-key",
        )
    with database_sessions() as session, pytest.raises(ConflictError):
        service.update_connection(
            session,
            actor=admin,
            cipher=cipher(),
            connection_id=identifier,
            expected_revision=1,
            api_key="wrong-key",
        )
    with database_sessions.begin() as session:
        assert service.resolve_model_secret(
            session, cipher=cipher(), actor=member, connection_id=identifier
        ) == {"api_key": "new-key"}
        service.disable_connection(session, actor=admin, connection_id=identifier)
    with database_sessions() as session, pytest.raises(ForbiddenError):
        service.resolve_connection(session, actor=member, connection_id=identifier)


def test_model_key_rotation_preserves_plaintext_and_catalog_revision(
    database_sessions, model_accounts
):
    from cryptography.fernet import Fernet

    admin, member = model_accounts
    old_key = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="
    rotated_cipher = SecretCipher(
        key=Fernet.generate_key().decode(), key_id="rotated", previous_keys={"test": old_key}
    )
    with database_sessions.begin() as session:
        connection = service.create_connection(
            session,
            actor=admin,
            cipher=cipher(),
            name="rotate-encryption",
            api_key="test-secret",
            base_url="https://example.test/v1",
            default_model="m",
            models=["m"],
        )
        identifier = connection.id
        service.grant_connection(
            session, actor=admin, connection_id=identifier, user_id=member.user_id, models=["m"]
        )
        assert service.reencrypt_model_keys(
            session, cipher=rotated_cipher, cursor=None, limit=100
        ) == (1, None)
    with database_sessions() as session:
        connection = service.get_connection(session, actor=admin, connection_id=identifier)
        assert connection.key_id == "rotated"
        assert connection.revision == 1
        assert service.resolve_model_secret(
            session, actor=member, cipher=rotated_cipher, connection_id=identifier
        ) == {"api_key": "test-secret"}
