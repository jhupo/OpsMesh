"""Run migrations on the connection supplied by the database plugin."""

from alembic import context
from sqlalchemy import Connection

config = context.config
connection = config.attributes.get("connection")

if connection is None:
    raise RuntimeError(
        "Migrations require a connection supplied by the database plugin"
    )


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=config.attributes["version_table"],
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations(connection)
