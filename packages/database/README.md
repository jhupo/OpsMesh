# opsmesh-database

Independent OpsMesh plugin for PostgreSQL connections, SQLAlchemy sessions and plugin-owned Alembic migrations. The import module is `opsmesh.plugins.database`.

The framework does not require this package. Install it only for plugins that need PostgreSQL. Database owns connections and migration mechanics; consumer plugins own their business tables.

## Installation

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/database
```

For a frozen Linux host, use the separate official plugin runtime archive and provide an existing PostgreSQL instance. The framework installer does not provision, check or migrate the database.

## Plugin contract

Declare `database = require("database")`. Put `DatabaseSchema(directory=..., head=...)` in `PluginMetadata.extra["database"]`. Framework plugin loading registers the migration declaration. Consumers retain their own native SQLAlchemy models and metadata.

`DatabaseAccess()` injects the shared database handle. Each concurrent branch opens its own session or transaction. Operations that must be atomic execute sequentially within one transaction; `AsyncSession` is not a concurrently shared resource.

Each plugin has its own migration version table. Startup checks versions and does not run schema upgrades automatically. Administrative commands are explicit:

```bash
opsmesh --manifest host.json --command opsmesh.plugins.database.commands:check
opsmesh --manifest host.json --command opsmesh.plugins.database.commands:migrate
```

These commands require the database and consumer plugins to be enabled in the host state database, with the appropriate configuration and permissions. Each activation reads current connection settings; close failures retain the pool for cleanup retry and reject new sessions.

Shared Alembic histories require the separately distributed repository maintenance tool. Stop applications using the database, supply a complete inventory including disabled owners, and load the connection secret through an environment variable:

```bash
python -m tools.database_history history.json --dsn-env OPSMESH_DATABASE_DSN
```

The tool holds the migration advisory lock and converts revision ledgers in one transaction without deleting business data or replaying schema upgrades. Migration owners remain stable across package renames. See the repository's database migration guide for the inventory format.

## License

MIT; see the package's included license files and third-party notices.
