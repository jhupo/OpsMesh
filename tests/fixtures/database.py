"""Shared on-disk database URLs for the native async SDK session in flow tests."""

from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4


def flow_database_url() -> str:
    path = Path(gettempdir()) / f"opsmesh-flow-{uuid4().hex}.sqlite"
    return f"sqlite+pysqlite:///{path.as_posix()}"
