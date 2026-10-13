"""PostgreSQL-backed HTTP fixture for Agent product contracts."""

import asyncio
from collections.abc import Generator

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.main import create_app
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.redis.dependencies import get_redis_client
from tests.fixtures.database import temporary_postgres_schema

TOKEN = "test-token"


@pytest.fixture
def agent_api(tmp_path) -> Generator[tuple[TestClient, Session], None, None]:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        app = create_app(
            Settings(
                environment="test",
                internal_api_token=TOKEN,
                database_url=engine.url.render_as_string(hide_password=False),
                storage_root=str(tmp_path / "artifacts"),
            )
        )
        redis = fakeredis.FakeRedis(decode_responses=True)

        def database_session() -> Generator[Session, None, None]:
            with sessions() as session:
                yield session

        app.dependency_overrides[get_db_session] = database_session
        app.dependency_overrides[get_redis_client] = lambda: redis
        client = TestClient(app)
        try:
            with sessions() as seed:
                yield client, seed
        finally:
            client.close()
            asyncio.run(app.state.task_event_engine.dispose())
            redis.close()
