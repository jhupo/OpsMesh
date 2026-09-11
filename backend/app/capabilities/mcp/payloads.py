import json
from hashlib import sha256


def canonical_payload(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, object]) -> str:
    return sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def hash_from_payload(payload: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def response_hash(response: dict[str, object] | None) -> str | None:
    if response is None:
        return None
    result = response.get("result")
    return payload_hash(result) if isinstance(result, dict) else None


def response_hash_from_payload(payload: dict[str, object] | None) -> str | None:
    existing = hash_from_payload(payload, "response_sha256")
    if existing is not None:
        return existing
    result = payload.get("result") if isinstance(payload, dict) else None
    return payload_hash(result) if isinstance(result, dict) else None


def error_code(error: dict[str, object] | None) -> str | None:
    if error is None:
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None
