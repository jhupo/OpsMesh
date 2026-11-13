"""Convert the shared ledger without running plugin lifecycle hooks."""

import argparse
import asyncio
import os
from pathlib import Path

from opsmesh.plugins.database import Database, Settings

from .history import convert_shared_history


async def convert(manifest: Path, dsn_environment: str) -> None:
    """Load a connection secret from the named environment variable."""
    dsn = os.environ.get(dsn_environment)
    if not dsn:
        raise ValueError("The database DSN environment variable is empty")
    database = Database(Settings(dsn=dsn))
    await database.startup()
    try:
        await convert_shared_history(database, manifest)
    finally:
        await database.shutdown()


def main() -> None:
    """Run the explicit manifest conversion with PostgreSQL locking."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--dsn-env", default="OPSMESH_DATABASE_DSN")
    arguments = parser.parse_args()
    asyncio.run(convert(arguments.manifest, arguments.dsn_env))


if __name__ == "__main__":
    main()
