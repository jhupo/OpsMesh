"""Authenticated encryption with explicit, externally supplied Fernet keys."""

import base64
import hmac
import json
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from opsmesh.shared.serialization import canonical_json, json_safe_value


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: str
    fingerprint: str
    key_id: str


class SecretCipher:
    def __init__(
        self, *, key: str, key_id: str, previous_keys: dict[str, str] | None = None
    ) -> None:
        if not key_id.strip() or key_id != key_id.strip():
            raise ValueError("Encryption key ID must be non-empty and have no surrounding spaces")
        keys = {key_id: key}
        for previous_id, previous_key in (previous_keys or {}).items():
            if (
                not previous_id.strip()
                or previous_id != previous_id.strip()
                or previous_id == key_id
            ):
                raise ValueError("Previous encryption keys must use distinct, non-empty IDs")
            keys[previous_id] = previous_key
        try:
            self._fernets = {}
            for identifier, value in keys.items():
                raw_key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
                if len(raw_key) != 32 or base64.urlsafe_b64encode(raw_key).decode("ascii") != value:
                    raise ValueError("Invalid Fernet key encoding")
                self._fernets[identifier] = Fernet(value.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                "Encryption keys must be URL-safe base64 encoded 32-byte keys"
            ) from exc
        self._key_id = key_id
        self._fingerprint_key = base64.urlsafe_b64decode(key.encode("ascii"))
        self._keyring = MultiFernet(list(self._fernets.values()))

    def encrypt_payload(self, payload: dict[str, object]) -> EncryptedSecret:
        normalized = canonical_json(payload).encode("utf-8")
        return EncryptedSecret(
            ciphertext=self._fernets[self._key_id].encrypt(normalized).decode("ascii"),
            fingerprint=f"sha256:{hmac.digest(self._fingerprint_key, normalized, 'sha256').hex()}",
            key_id=self._key_id,
        )

    def decrypt_payload(self, ciphertext: str, *, key_id: str | None = None) -> dict[str, object]:
        if key_id is not None and key_id not in self._fernets:
            raise ValueError("Secret payload uses an unknown encryption key ID")
        cipher = self._keyring if key_id is None else self._fernets[key_id]
        try:
            plaintext = cipher.decrypt(ciphertext.encode("ascii"))
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError("Secret payload could not be decrypted") from exc
        value = json_safe_value(json.loads(plaintext))
        if not isinstance(value, dict):
            raise ValueError("Secret payload must decrypt to an object")
        return value

    @property
    def current_key_id(self) -> str:
        return self._key_id
