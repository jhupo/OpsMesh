"""Shared fixtures: PostgreSQL-backed tests run only when a test database is configured."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
import sqlalchemy.ext.asyncio
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from opsmesh.bootstrap.models import register_models
from opsmesh.config import Settings
from opsmesh.shared.db.session import create_database_engine
from opsmesh.shared.security.secrets import SecretCipher

TEST_DATABASE_URL_ENV = "OPSMESH_TEST_DATABASE_URL"
TEST_STORAGE_ROOT = ".opsmesh-test-storage"


def test_database_url() -> str | None:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    return url or None


def require_real_postgres() -> str:
    url = test_database_url()
    if url is None:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")
    return url


def build_test_settings(database_url: str) -> Settings:
    return Settings(database_url=database_url, storage_root=TEST_STORAGE_ROOT)


@pytest.fixture(scope="session")
def database_url() -> str:
    return require_real_postgres()


@pytest.fixture(scope="session")
def database_engine(database_url: str) -> Iterator[Engine]:
    engine = create_database_engine(database_url, pool_size=2, max_overflow=2)
    register_models().create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def database_sessions(database_engine: Engine) -> Iterator[sessionmaker]:
    sessions = sessionmaker(
        bind=database_engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": dispatch_cipher(),
            "dispatch_trace_context": lambda: {},
        },
    )
    yield sessions
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE temporal_dispatches, http_creation_receipts"))


@pytest.fixture
def async_database_sessions(database_url: str) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        make_url(database_url).set(drivername="postgresql+psycopg"),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
    )
    sessions = async_sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": dispatch_cipher(),
            "dispatch_trace_context": lambda: {},
        },
    )
    yield sessions

    async def release() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE temporal_dispatches, http_creation_receipts")
            )
        await engine.dispose()

    asyncio.run(release())


def dispatch_cipher() -> SecretCipher:
    return SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")
