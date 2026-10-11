"""Prevent concurrent SDK processes from executing the same run."""

import os
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

ROOT = Path("/tmp/opsmesh-runs")


def register(run_id: UUID) -> BinaryIO:
    import fcntl

    root = ROOT / str(os.getuid())
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (root / f"{run_id.hex}.lock").open("wb")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        lock.close()
        raise
    return lock
