"""Opt-in real Postgres checks, separate from portable SQLite test processes."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.admin.updates.models import PlatformInstallation, PlatformUpdateEvent
from backend.app.admin.updates.service import UpdateService

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_DELIVERY_POSTGRES_TEST") != "1",
    reason="Requires explicit disposable PostgreSQL delivery test database",
)


def test_release_schema_roundtrip_has_no_metadata_drift() -> None:
    assert os.environ.get("OPSMESH_ENVIRONMENT") == "test"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", os.environ["OPSMESH_DATABASE_URL"].replace("%", "%%"))
    command.downgrade(config, "0071_platform_delivery")
    command.upgrade(config, "head")
    command.check(config)


def test_installation_admission_lock_blocks_exclusive_maintenance() -> None:
    assert os.environ.get("OPSMESH_ENVIRONMENT") == "test"
    engine = create_engine(os.environ["OPSMESH_DATABASE_URL"])
    with Session(engine) as holder:
        assert holder.scalar(select(PlatformInstallation).with_for_update(read=True)) is not None

        def exclusive_attempt() -> None:
            with Session(engine) as contender:
                contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError):
                    contender.scalar(select(PlatformInstallation).with_for_update())

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(exclusive_attempt).result(timeout=5)
    engine.dispose()


def test_update_event_is_worm_and_plan_is_transactional() -> None:
    assert os.environ.get("OPSMESH_ENVIRONMENT") == "test"
    engine = create_engine(os.environ["OPSMESH_DATABASE_URL"])
    with Session(engine) as session:
        service = UpdateService(session)
        job = service.request(tag="v0.2.0", action="update", key=uuid4().hex)
        event = session.scalar(
            select(PlatformUpdateEvent).where(PlatformUpdateEvent.job_id == job.id)
        )
        assert event is not None
        with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
            session.execute(
                text("DELETE FROM platform_update_events WHERE id = :id"), {"id": event.id}
            )
            # The failed statement aborts only its savepoint; the enclosing fixture rolls back.
    engine.dispose()
