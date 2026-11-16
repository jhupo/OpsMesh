"""Shared validation for channel configuration and callback paths."""

from __future__ import annotations

import re

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def connection_segment(value: str, label: str) -> str:
    """Validate one namespace component used in a callback path."""
    if not isinstance(value, str) or not _SEGMENT.fullmatch(value):
        raise ValueError(f"{label} must be a URL-safe identifier")
    if ".." in value:
        raise ValueError(f"{label} must not contain '..'")
    return value
