from __future__ import annotations


def dict_payload(value: object, *, context: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{context} arguments must be an object")
    return dict(value)


def string_tuple(value: object, *, context: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{context} runtime_allowed_tools must be a list")
    return tuple(item for item in value if isinstance(item, str) and item)

