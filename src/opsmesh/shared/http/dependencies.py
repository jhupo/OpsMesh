"""Provide assembled database and Redis connections to route handlers."""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, cast

from fastapi import HTTPException, Request
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.shared.redis.cache import RedisJsonCache

if TYPE_CHECKING:
    RedisClient = Redis[str]
else:
    RedisClient = Redis


def get_db_session(request: Request) -> Generator[Session, None, None]:
    sessions = cast(sessionmaker[Session], request.app.state.database_sessions)
    with sessions() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def get_redis_client(request: Request) -> RedisClient | None:
    return cast(RedisClient | None, request.app.state.redis_client)


def get_json_cache(request: Request) -> RedisJsonCache:
    cache = cast(RedisJsonCache | None, request.app.state.json_cache)
    if cache is None:
        raise HTTPException(status_code=503, detail="Redis cache is unavailable")
    return cache
