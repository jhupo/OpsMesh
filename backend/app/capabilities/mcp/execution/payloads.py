from backend.app.shared.utils import payload_hash


def hash_from_payload(payload: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def response_hash(response: dict[str, object] | None) -> str | None:
    return payload_hash(response) if response is not None else None


def response_hash_from_payload(payload: dict[str, object] | None) -> str | None:
    return response_hash(payload)


def error_code(error: dict[str, object] | None) -> str | None:
    if error is None:
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None
