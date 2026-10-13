"""Portable unit database creation mirrors required Alembic seed data."""

import asyncio
import sys
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Table, event

from opsmesh.bootstrap.models import register_models
from opsmesh.governance.policies.models import PlatformPolicy
from opsmesh.platform.settings.configuration import CONFIGURATION_KEY, OperationalConfiguration
from opsmesh.platform.updates.models import PlatformInstallation
from tests.fixtures.database import managed_product_databases  # noqa: F401

register_models()


def pytest_configure() -> None:
    # Psycopg's Windows async transport requires a selector loop.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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


@pytest.fixture(autouse=True)
def seed_operational_configuration() -> Iterator[None]:
    def seed(table: Table, connection: Connection, **_: object) -> None:
        connection.execute(
            table.insert().values(
                id=uuid4(),
                policy_key=CONFIGURATION_KEY,
                status="active",
                value=OperationalConfiguration().model_dump(mode="json"),
                description="Operational configuration",
            )
        )

    table = PlatformPolicy.__table__
    event.listen(table, "after_create", seed)
    try:
        yield
    finally:
        event.remove(table, "after_create", seed)
