import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app.db.base import Base
from backend.app.runs.event_writer import RunEventWriter
from backend.app.runs.models import AgentRun
from backend.tests.test_postgres_scheduler_concurrency import (
    POSTGRES_TEST_URL_ENV,
    _metadata_has_sqlite_json_columns,
    _seed_scheduler_fixture,
    _temporary_postgres_schema,
)


@pytest.mark.skipif(not os.getenv(POSTGRES_TEST_URL_ENV), reason="PostgreSQL URL not configured")
def test_parallel_run_events_allocate_distinct_sequences() -> None:
    if _metadata_has_sqlite_json_columns():
        pytest.skip("Run PostgreSQL test separately from SQLite tests")
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        fixture = _seed_scheduler_fixture(factory)
        with factory() as session:
            run = AgentRun(workspace_id=fixture.workspace_id)
            session.add(run)
            session.commit()
            run_id = run.id
        barrier = threading.Barrier(2)

        def append() -> int:
            with factory() as session:
                barrier.wait(timeout=10)
                event = RunEventWriter(session).append(
                    workspace_id=fixture.workspace_id, run_id=run_id,
                    event_type="test", message="parallel",
                )
                session.commit()
                return event.sequence

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append) for _ in range(2)]
            assert sorted(future.result(timeout=20) for future in futures) == [1, 2]
