from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet, InvalidToken

from backend.app.security.redaction import REDACTED_VALUE, is_sensitive_payload_key

_URL_CONFIG_KEYS = {
    "address",
    "endpoint",
    "url",
    "vault_addr",
    "vault_url",
}
_SAFE_METADATA_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._-"
)


@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: str
    fingerprint: str
    key_id: str
    secret_version: str = "v1"
    rotation_state: str = "current"


@dataclass(frozen=True)
class SecretMetadata:
    storage: str
    provider: str
    configured: bool
    reference_kind: str | None = None
    reference_version: str | None = None
    encryption_key_id: str | None = None
    fingerprint_configured: bool = False
    rotation_state: str = "not_configured"
    raw_secret_exposed: bool = False

    def to_api_dict(self) -> dict[str, object]:
        return {
            "storage": self.storage,
            "provider": self.provider,
            "configured": self.configured,
            "reference_kind": self.reference_kind,
            "reference_version": self.reference_version,
            "encryption_key_id": self.encryption_key_id,
            "fingerprint_configured": self.fingerprint_configured,
            "rotation_state": self.rotation_state,
            "raw_secret_exposed": self.raw_secret_exposed,
        }


class SecretEncryptionService:
    def __init__(
        self,
        *,
        secret: str,
        key_id: str,
        previous_secrets: dict[str, str] | None = None,
    ) -> None:
        self._secret = secret
        self._key_id = key_id
        self._fernet = self._fernet_for_secret(secret)
        self._previous_fernets = {
            previous_key_id: self._fernet_for_secret(previous_secret)
            for previous_key_id, previous_secret in (previous_secrets or {}).items()
            if previous_key_id and previous_secret
        }

    def encrypt_payload(self, payload: dict[str, object]) -> EncryptedSecret:
        normalized = self._normalize(payload)
        ciphertext = self._fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")
        return EncryptedSecret(
            ciphertext=ciphertext,
            fingerprint=self._fingerprint(normalized),
            key_id=self._key_id,
        )

    def decrypt_payload(self, ciphertext: str, *, key_id: str | None = None) -> dict[str, object]:
        plaintext = self._decrypt(ciphertext, key_id=key_id)
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise ValueError("Secret payload must decrypt to an object")
        return value

    @property
    def current_key_id(self) -> str:
        return self._key_id

    def needs_rotation(self, key_id: str | None) -> bool:
        return bool(key_id) and key_id != self._key_id

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

    @staticmethod
    def _fernet_for_secret(secret: str) -> Fernet:
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)

    def _decrypt(self, ciphertext: str, *, key_id: str | None) -> str:
        token = ciphertext.encode("utf-8")
        candidates: list[Fernet] = []
        if key_id is not None and key_id in self._previous_fernets:
            candidates.append(self._previous_fernets[key_id])
        candidates.append(self._fernet)
        candidates.extend(
            fernet
            for previous_key_id, fernet in self._previous_fernets.items()
            if previous_key_id != key_id
        )
        last_error: InvalidToken | None = None
        for fernet in candidates:
            try:
                return fernet.decrypt(token).decode("utf-8")
            except InvalidToken as exc:
                last_error = exc
        raise ValueError("Secret payload could not be decrypted") from last_error


def hosted_secret_metadata(
    *,
    provider: str = "hosted",
    encryption_key_id: str | None,
    secret_fingerprint: str | None,
) -> SecretMetadata:
    return SecretMetadata(
        storage="hosted_encrypted",
        provider=provider,
        configured=bool(secret_fingerprint),
        encryption_key_id=encryption_key_id,
        fingerprint_configured=bool(secret_fingerprint),
        rotation_state="current" if secret_fingerprint else "not_configured",
    )


def external_vault_reference_metadata(
    *,
    provider: str,
    external_ref: str,
) -> SecretMetadata:
    configured = bool(external_ref)
    return SecretMetadata(
        storage="external_vault",
        provider=provider,
        configured=configured,
        reference_kind=vault_reference_kind(external_ref) if configured else None,
        reference_version=vault_reference_version(external_ref) if configured else None,
        rotation_state="provider_managed" if configured else "not_configured",
    )


def vault_reference_kind(external_ref: str) -> str | None:
    if not external_ref:
        return None
    parsed = urlparse(external_ref)
    if parsed.scheme:
        return _safe_metadata_token(parsed.scheme)
    if ":" in external_ref:
        return _safe_metadata_token(external_ref.split(":", 1)[0])
    if "/" in external_ref:
        return _safe_metadata_token(external_ref.split("/", 1)[0])
    return "reference"


def vault_reference_version(external_ref: str) -> str | None:
    parsed = urlparse(external_ref)
    query = parse_qs(parsed.query)
    for key in ("version", "version_id", "secret_version"):
        values = query.get(key)
        if values:
            return _safe_metadata_token(values[-1])
    if parsed.fragment:
        fragment = parsed.fragment
        if "=" in fragment:
            _, fragment = fragment.split("=", 1)
        return _safe_metadata_token(fragment)
    path_parts = [part for part in parsed.path.split("/") if part]
    for marker in ("versions", "version"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if index + 1 < len(path_parts):
                return _safe_metadata_token(path_parts[index + 1])
    leaf = path_parts[-1] if path_parts else external_ref.rsplit("/", 1)[-1]
    if "@" in leaf:
        return _safe_metadata_token(leaf.rsplit("@", 1)[1])
    return None


def redact_secret_provider_configs(
    configs: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {
        str(provider): _redact_secret_provider_config(config)
        for provider, config in configs.items()
    }


def _redact_secret_provider_config(config: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in config.items():
        key_text = str(key)
        if is_sensitive_payload_key(key_text):
            redacted[key_text] = REDACTED_VALUE
            continue
        if _is_url_config_key(key_text) and isinstance(value, str):
            redacted[f"{key_text}_configured"] = bool(value)
            redacted[f"{key_text}_host"] = _url_host(value)
            continue
        if isinstance(value, dict):
            redacted[key_text] = _redact_secret_provider_config(value)
            continue
        if isinstance(value, list):
            redacted[key_text] = [_redact_secret_provider_config_item(item) for item in value]
            continue
        redacted[key_text] = value
    return redacted


def _redact_secret_provider_config_item(value: object) -> object:
    if isinstance(value, dict):
        return _redact_secret_provider_config(value)
    return value


def _is_url_config_key(key: str) -> bool:
    return key.lower().replace("-", "_") in _URL_CONFIG_KEYS


def _url_host(value: str) -> str | None:
    parsed = urlparse(value)
    return parsed.netloc or None


def _safe_metadata_token(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "configured"
    if all(char in _SAFE_METADATA_CHARS for char in stripped):
        return stripped[:120]
    return "configured"
