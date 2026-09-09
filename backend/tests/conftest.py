"""Portable unit database creation mirrors required Alembic seed data."""
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Table, event

from backend.app.admin.updates.models import PlatformInstallation


@pytest.fixture(autouse=True)
def seed_platform_installation() -> Iterator[None]:
    def seed(table: Table, connection: Connection, **_: object) -> None:
        connection.execute(table.insert().values(id=1, maintenance=False, release_manifest={}))

    table = PlatformInstallation.__table__
    event.listen(table, "after_create", seed)
    try:
        yield
    finally:
        event.remove(table, "after_create", seed)
