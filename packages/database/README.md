# opsmesh-database

Independent OpsMesh plugin for PostgreSQL connections, SQLAlchemy sessions and plugin-owned Alembic migrations. The import module and configuration namespace are `opsmesh.plugins.database`.

The framework does not require this package. Install it only for plugins that need PostgreSQL. Database owns connections and migration mechanics; consumer plugins own their business tables.

## Installation

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/database
```

For a frozen Linux host, use the separate official plugin runtime archive and provide an existing PostgreSQL instance. The framework installer does not provision, check or migrate the database.

## Plugin contract

Declare `database = require("database")`. Put a `DatabaseSchema` containing native SQLAlchemy metadata, the migration directory and the required head in `PluginMetadata.extra["database"]`. Framework plugin loading registers the declaration; consumers do not manually register models.

`DatabaseAccess()` injects the shared database handle. Each concurrent branch opens its own session or transaction. Operations that must be atomic execute sequentially within one transaction; `AsyncSession` is not a concurrently shared resource.

Each plugin has its own migration version table. Startup checks versions and does not run schema upgrades automatically. Administrative commands are explicit:

```bash
opsmesh --manifest host.json --command opsmesh.plugins.database.commands:check
opsmesh --manifest host.json --command opsmesh.plugins.database.commands:migrate
```

These commands require the enabled database and consumer plugins in the manifest, plus the appropriate environment and permissions. Historical shared Alembic databases use the explicit conversion command described in `docs/database-migrations.md`; never rename existing business migration owners just because distribution names changed.

## License

MIT; see the package's included license files and third-party notices.
