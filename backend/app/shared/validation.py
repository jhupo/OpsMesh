from __future__ import annotations


def positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default
