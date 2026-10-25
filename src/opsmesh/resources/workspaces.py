import os
import re
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from opsmesh.contracts import UserIdentity
from opsmesh.quotas.storage import check
from opsmesh.shared.errors import NotFoundError
from opsmesh.users.service import require_owner

from .models import Workspace


def directory(root: Path, workspace: Workspace) -> Path:
    return root / "users" / workspace.owner_id / "workspaces" / workspace.id


def safe_path(root: Path, name: str) -> Path:
    parts = name.split("/")
    if not parts or any(
        not part
        or part in {".", ".."}
        or re.search(r'[\\:<>"|?*\x00-\x1f]', part)
        or part[-1:] in {" ", "."}
        or part.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in parts
    ):
        raise ValueError("Invalid resource path")
    target = root.joinpath(*parts)
    for path in [root, *target.parents, target]:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Resource links are not allowed")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Resource path escapes its workspace")
    return target


def owned(db: Session, user: UserIdentity, workspace_id: str) -> Workspace:
    row = db.get(Workspace, workspace_id)
    if row is None:
        raise NotFoundError("Workspace not found")
    require_owner(user, row.owner_id)
    return row


def write(db: Session, root: Path, workspace: Workspace, name: str, content: bytes) -> None:
    write_file(db, root, workspace.owner_id, directory(root, workspace), name, content)


def write_file(
    db: Session,
    root: Path,
    owner_id: str,
    folder: Path,
    name: str,
    content: bytes,
) -> None:
    target = safe_path(folder, name)
    check(
        db,
        root,
        owner_id,
        len(content) - (target.stat().st_size if target.is_file() else 0),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
