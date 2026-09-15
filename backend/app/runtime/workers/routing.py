from uuid import UUID


def required_string(payload: dict[str, object], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} is missing {key}")
    return value.strip()


def required_int(
    payload: dict[str, object],
    key: str,
    *,
    context: str,
    minimum: int | None = None,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context} is missing a valid {key}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context} {key} must be at least {minimum}")
    return value


def positive_int(value: object, *, default: int, key: str, context: str) -> int:
    if value is None:
        return default
    if isinstance(value, int) and value > 0:
        return value
    raise ValueError(f"{context} {key} must be a positive integer")


def positive_float(
    value: object,
    *,
    default: float,
    key: str,
    context: str,
    maximum: float | None = None,
) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a positive number")
    if isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{key} must be a positive number") from exc
    else:
        raise ValueError(f"{key} must be a positive number")
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive number")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{key} must be at most {maximum:g}")
    return parsed


def optional_uuid(value: object, *, context: str) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{context} UUID fields must be strings")
    return UUID(value)


def required_uuid(payload: dict[str, object], key: str, *, context: str) -> UUID:
    parsed = optional_uuid(payload.get(key), context=context)
    if parsed is None:
        raise ValueError(f"{context} is missing {key}")
    return parsed


def bool_value(value: object, default: bool, *, context: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"{context} boolean fields must be booleans")


def string_list(value: object, *, key: str, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{context} {key} must be a non-empty list")
    items = [item for item in value if isinstance(item, str) and item]
    if len(items) != len(value):
        raise ValueError(f"{context} {key} must contain only strings")
    return items


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
