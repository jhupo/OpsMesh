from pathlib import Path

from sqlalchemy.orm import Session

from opsmesh.shared.errors import QuotaExceededError

from .tokens import quota


def size(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for path in directory.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Links are not permitted in managed storage")
        if path.is_file():
            total += path.stat().st_size
    return total


def check(db: Session, root: Path, user_id: str, extra: int) -> None:
    limits = quota(db, user_id)
    if size(root / "users" / user_id) + extra > limits.storage_bytes:
        raise QuotaExceededError("Storage quota exhausted")
