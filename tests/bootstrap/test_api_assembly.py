"""S2 bootstrap assembly tests: startup failure releases resources in reverse order."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

import opsmesh.bootstrap.api as api_module
from opsmesh.config import Settings


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://opsmesh:secret@db.invalid:5432/opsmesh",
        storage_root=".opsmesh-test-storage",
    )


def test_startup_failure_disposes_engine_before_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(api_module, "create_database_engine", lambda *a, **k: engine)

    def failing_resources(app_settings: Settings):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(api_module, "build_api_resources", failing_resources)

    app = api_module.create_app(settings())
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def run() -> AsyncIterator[None]:
        async with lifespan(app):
            yield

    with pytest.raises(RuntimeError, match="redis unavailable"):
        asyncio.run(run().__aenter__())
    assert engine.disposed, "the database engine must be released on startup failure"


def test_application_lifespan_sets_state_and_releases_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(api_module, "create_database_engine", lambda *a, **k: engine)

    app = api_module.create_app(settings())
    lifespan = app.router.lifespan_context

    async def run() -> None:
        async with lifespan(app):
            assert app.state.storage_root == ".opsmesh-test-storage"
            assert app.state.database_sessions is not None
            assert app.state.json_cache is not None

    asyncio.run(run())
    assert engine.disposed, "the database engine must be released on shutdown"
