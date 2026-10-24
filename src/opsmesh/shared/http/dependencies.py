"""Provide assembled database sessions to route handlers."""

from __future__ import annotations

from collections.abc import Generator
from typing import cast

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.shared.security.encryption import SecretCipher


def secret_cipher(request: Request) -> SecretCipher:
    cipher = getattr(request.app.state, "secret_cipher", None)
    if not isinstance(cipher, SecretCipher):
        raise HTTPException(503, "Platform encryption is unavailable")
    return cipher


def get_db_session(request: Request) -> Generator[Session, None, None]:
    sessions = cast(sessionmaker[Session], request.app.state.database_sessions)
    with sessions() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
