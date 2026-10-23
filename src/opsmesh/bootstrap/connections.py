"""Build database and Redis resources; owners receive explicit options."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import sqlalchemy.ext.asyncio
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.policies import transaction_dependencies
from opsmesh.config import Settings
from opsmesh.shared.db.session import create_database_engine
from opsmesh.shared.redis.client import create_redis_client
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.rate_limits import FixedWindowRateLimiter
from opsmesh.telemetry.context import capture_trace_context

POSTGRES_PSYCOPG_DRIVER = "postgresql+psycopg"


def secret_cipher(settings: Settings) -> SecretCipher:
    return SecretCipher(
        key=settings.credential_encryption_key,
        key_id=settings.credential_encryption_key_id,
        previous_keys=settings.credential_encryption_previous_keys,
    )


@dataclass(frozen=True)
class APIResources:
    redis_client: Redis[str]
    rate_limiter: FixedWindowRateLimiter


def build_api_resources(settings: Settings) -> APIResources:
    redis_client = create_redis_client(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        socket_connect_timeout_seconds=settings.redis_socket_connect_timeout_seconds,
        health_check_interval_seconds=settings.redis_health_check_interval_seconds,
    )
    return APIResources(
        redis_client=redis_client,
        rate_limiter=FixedWindowRateLimiter.from_redis(
            redis_client,
            key_prefix=settings.redis_key_prefix,
        ),
    )


@dataclass(frozen=True)
class DatabaseResources:
    engine: Engine
    sessions: sessionmaker[Session]
    cipher: SecretCipher


def build_database_resources(settings: Settings) -> DatabaseResources:
    engine = create_database_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
        pool_recycle_seconds=settings.database_pool_recycle_seconds,
        statement_timeout_ms=settings.database_statement_timeout_ms,
    )
    cipher = secret_cipher(settings)
    return DatabaseResources(
        engine=engine,
        sessions=sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            info={
                "dispatch_cipher": cipher,
                "dispatch_trace_context": capture_trace_context,
                **transaction_dependencies(),
            },
        ),
        cipher=cipher,
    )


@dataclass(frozen=True)
class TemporalDatabase:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def temporal_database(settings: Settings) -> AsyncIterator[TemporalDatabase]:
    database_url = make_url(settings.database_url).set(drivername=POSTGRES_PSYCOPG_DRIVER)
    configured_options = database_url.query.get("options", "")
    if not isinstance(configured_options, str):
        raise ValueError("Database connection options must be a single string")
    options = (
        f"{configured_options} -c statement_timeout={settings.database_statement_timeout_ms}"
    ).strip()
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=settings.temporal_outbox_database_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args={"options": options},
    )
    try:
        yield TemporalDatabase(engine, async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
