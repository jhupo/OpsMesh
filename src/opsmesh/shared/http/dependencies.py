"""Provide assembled database sessions to route handlers."""

from __future__ import annotations

from collections.abc import Generator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_db_session(request: Request) -> Generator[Session, None, None]:
    sessions = cast(sessionmaker[Session], request.app.state.database_sessions)
    with sessions() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
