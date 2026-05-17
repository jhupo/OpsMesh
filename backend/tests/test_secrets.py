from backend.app.secrets.service import SecretEncryptionService


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
