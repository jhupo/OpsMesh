import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.users.models import User
from opsmesh.users.passwords import hash_password


@dataclass(frozen=True)
class InitialCredentials:
    user_id: str
    username: str
    password: str
    generated: bool


def save_initial_credentials(directory: Path, credentials: InitialCredentials) -> Path:
    """Hand initial credentials to the local operator without sending them to logs."""
    path = directory / f"initial-admin-{credentials.user_id}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"username": credentials.username, "password": credentials.password}, stream)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def initialize_admin(
    db: Session, username: str | None, password: str | None
) -> InitialCredentials | None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('bootstrap-admin', 0))"))
    if any("admin" in roles for roles in db.scalars(select(User.roles))):
        return None
    name = username or f"admin_{secrets.token_hex(4)}"
    credential = password or secrets.token_urlsafe(24)
    row = User(name=name, roles=["admin", "user"], password_hash=hash_password(credential))
    db.add(row)
    db.flush()
    return InitialCredentials(row.id, name, credential, username is None or password is None)
