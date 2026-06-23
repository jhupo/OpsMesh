def dict_value(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def set_value(value: object) -> set[object]:
    return value if isinstance(value, set) else set()
