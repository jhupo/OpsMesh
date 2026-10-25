import asyncio
import os
import stat
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from opsmesh.shared.errors import QuotaExceededError

from .tokens import quota


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if sys.platform == "win32":
        return bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def size(directory: Path, *, limit: int | None = None) -> int:
    try:
        root = directory.lstat()
    except FileNotFoundError:
        return 0
    if _is_link(root):
        raise ValueError("Links are not permitted in managed storage")
    total = 0
    pending = [directory]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    raise ValueError("Links are not permitted in managed storage")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if limit is not None and total > limit:
                        return total
    return total


def check(db: Session, root: Path, user_id: str, extra: int) -> None:
    limits = quota(db, user_id)
    remaining = limits.storage_bytes - extra
    if remaining < 0 or size(root / "users" / user_id, limit=remaining) > remaining:
        raise QuotaExceededError("Storage quota exhausted")


async def check_async(sessions: sessionmaker, root: Path, user_id: str, extra: int) -> None:
    def verify() -> None:
        with sessions.begin() as db:
            check(db, root, user_id, extra)

    # Own the transaction in its thread and finish it before propagating cancellation.
    operation = asyncio.create_task(asyncio.to_thread(verify))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise
