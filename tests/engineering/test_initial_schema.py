"""The complete migration chain must reproduce all current model contracts."""

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, inspect, text

from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.schema import RETIRED_TABLES, include_schema_object


def test_initial_schema_matches_every_registered_product_table(database_engine):
    metadata = register_models()
    with database_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        )
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        assert tables == set(metadata.tables) | RETIRED_TABLES
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "include_object": include_schema_object,
            },
        )
        assert compare_metadata(context, metadata) == []
        inspector = inspect(connection)
        for table in metadata.sorted_tables:
            actual_checks = {item["name"] for item in inspector.get_check_constraints(table.name)}
            expected_checks = {
                database_engine.dialect.identifier_preparer.format_constraint(constraint)
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            assert actual_checks == expected_checks, table.name
