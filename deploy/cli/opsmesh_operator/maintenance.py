from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def maintenance_lock(root: Path) -> Iterator[None]:
    """Serialize host recovery and backup operations across operator processes."""
    path = root / ".maintenance.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another host maintenance operation is active") from exc
        yield
    finally:
        os.close(descriptor)
