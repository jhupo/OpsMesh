"""The sole V4 migration must reproduce the final model schema on PostgreSQL."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, inspect, text

from opsmesh.bootstrap.models import register_models


def test_initial_schema_matches_every_registered_product_table(database_engine):
    metadata = register_models()
    with database_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_v4_initial"
        )
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        assert tables == set(metadata.tables)
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
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
