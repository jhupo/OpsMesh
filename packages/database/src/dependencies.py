"""Inject the database owner without sharing mutable sessions across tasks."""

from typing import Any

from opsmesh.params import Depends

from .connections import get_database


def DatabaseAccess() -> Any:
    """Inject Database; each concurrent branch opens its own session scope."""
    return Depends(get_database)
