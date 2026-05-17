from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from cryptography.fernet import Fernet


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: str
    fingerprint: str
    key_id: str


class SecretEncryptionService:
    def __init__(self, *, secret: str, key_id: str) -> None:
        self._secret = secret
        self._key_id = key_id
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt_payload(self, payload: dict[str, object]) -> EncryptedSecret:
        normalized = self._normalize(payload)
        ciphertext = self._fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")
        return EncryptedSecret(
            ciphertext=ciphertext,
            fingerprint=self._fingerprint(normalized),
            key_id=self._key_id,
        )

    def decrypt_payload(self, ciphertext: str) -> dict[str, object]:
        plaintext = self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise ValueError("Secret payload must decrypt to an object")
        return value

    def _fingerprint(self, normalized_payload: str) -> str:
        digest = hmac.new(
            self._secret.encode("utf-8"),
            normalized_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"sha256:{digest}"

    @staticmethod
    def _normalize(payload: dict[str, object]) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
