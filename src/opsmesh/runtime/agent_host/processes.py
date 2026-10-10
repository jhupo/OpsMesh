"""Identify and reap one SDK process group without touching its neighbours."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

ROOT = Path("/tmp/opsmesh-runs")


def register(run_id: UUID) -> BinaryIO:
    import fcntl

    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (ROOT / f"{run_id.hex}.lock").open("wb")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        lock.close()
        raise
    pid = os.getpid()
    path = ROOT / f"{run_id.hex}.json"
    path.write_text(json.dumps({"pid": pid, "start": _start_time(pid)}))
    path.chmod(0o600)
    return lock


def _start_time(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def stop(run_id: UUID) -> None:
    path = ROOT / f"{run_id.hex}.json"
    if not path.exists():
        return
    record = json.loads(path.read_text())
    pid = record["pid"]
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError("Invalid SDK process identity")
    current = _start_time(pid)
    if current is not None and current != record["start"]:
        raise ValueError("SDK process identity was reused")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            time.sleep(0.1)
    path.unlink(missing_ok=True)


if __name__ == "__main__":
    stop(UUID(sys.argv[1]))
