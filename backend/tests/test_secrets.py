from backend.app.secrets.service import (
    SecretEncryptionService,
    external_vault_reference_metadata,
    redact_secret_provider_configs,
)


def test_secret_encryption_round_trip_and_fingerprint_are_stable() -> None:
    service = SecretEncryptionService(secret="test-secret", key_id="test")
    payload = {"api_key": "sk-secret", "nested": {"scope": "write"}}

    encrypted = service.encrypt_payload(payload)
    decrypted = service.decrypt_payload(encrypted.ciphertext)
    repeated = service.encrypt_payload(payload)

    assert decrypted == payload
    assert "sk-secret" not in encrypted.ciphertext
    assert encrypted.fingerprint == repeated.fingerprint
    assert encrypted.key_id == "test"
    assert encrypted.secret_version == "v1"
    assert encrypted.rotation_state == "current"


def test_secret_encryption_keyring_decrypts_previous_key_payloads() -> None:
    old_service = SecretEncryptionService(secret="old-secret", key_id="old")
    new_service = SecretEncryptionService(
        secret="new-secret",
        key_id="new",
        previous_secrets={"old": "old-secret"},
    )
    payload = {"api_key": "sk-old-secret"}

    encrypted = old_service.encrypt_payload(payload)

    assert new_service.decrypt_payload(encrypted.ciphertext, key_id=encrypted.key_id) == payload
    assert new_service.needs_rotation(encrypted.key_id) is True
    assert new_service.encrypt_payload(payload).key_id == "new"


def test_external_vault_reference_metadata_is_redacted_and_versioned() -> None:
    metadata = external_vault_reference_metadata(
        provider="vault",
        external_ref="vault://kv/data/image-api-key?version=42",
    ).to_api_dict()

    assert metadata == {
        "storage": "external_vault",
        "provider": "vault",
        "configured": True,
        "reference_kind": "vault",
        "reference_version": "42",
        "encryption_key_id": None,
        "fingerprint_configured": False,
        "rotation_state": "provider_managed",
        "raw_secret_exposed": False,
    }
    assert "image-api-key" not in str(metadata)


def test_secret_provider_config_redacts_urls_and_tokens() -> None:
    redacted = redact_secret_provider_configs(
        {
            "vault": {
                "url": "https://vault.example.test/v1/secret?token=raw",
                "token": "vault-token",
                "mount": "kv",
                "headers": {"Authorization": "Bearer secret"},
            }
        }
    )

    assert redacted["vault"] == {
        "url_configured": True,
        "url_host": "vault.example.test",
        "token": "[redacted]",
        "mount": "kv",
        "headers": "[redacted]",
    }
    assert "vault-token" not in str(redacted)
    assert "secret?token=raw" not in str(redacted)
