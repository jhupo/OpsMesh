"""Isolated PostgreSQL databases for product flows and concurrent transactions."""

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, close_all_sessions, sessionmaker

from opsmesh.shared.config import get_settings
from opsmesh.shared.db.base import Base

_product_databases: ContextVar[ExitStack] = ContextVar("product_databases")


@contextmanager
def temporary_postgres_schema() -> Iterator[Engine]:
    database_url = os.getenv("OPSMESH_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("OPSMESH_TEST_POSTGRES_URL is required for product database tests")
    schema = f"product_test_{uuid4().hex}"
    admin = create_engine(database_url)
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(url, pool_size=4, max_overflow=4)
    try:
        with admin.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(autouse=True)
def managed_product_databases(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Own all helper-created databases for a test, including failure cleanup."""
    with ExitStack() as stack:
        token = _product_databases.set(stack)
        current_url = os.environ.get("OPSMESH_DATABASE_URL")
        if current_url is None:
            monkeypatch.delenv("OPSMESH_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("OPSMESH_DATABASE_URL", current_url)
        try:
            yield
        finally:
            close_all_sessions()
            _product_databases.reset(token)
            get_settings.cache_clear()


def product_engine() -> Engine:
    engine = _product_databases.get().enter_context(temporary_postgres_schema())
    # API event readers must use the same database as the overridden HTTP session.
    os.environ["OPSMESH_DATABASE_URL"] = engine.url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    return engine


def product_session() -> Session:
    engine = product_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()
