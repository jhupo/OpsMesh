"""Real PostgreSQL transaction fixture for Agent governance tests."""

from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.shared.db.base import Base
from tests.fixtures.database import temporary_postgres_schema


@pytest.fixture
def agent_database() -> Generator[Session, None, None]:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            yield session
