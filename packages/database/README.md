# opsmesh-database

**Verified against the current runtime on 2026-11-20.** This independent
plugin owns PostgreSQL connections, SQLAlchemy sessions and plugin-owned
Alembic migrations. Its managed wheel exports the top-level module `database`.

The framework does not require this package. Install it only for plugins that need PostgreSQL. Database owns connections and migration mechanics; consumer plugins own their business tables.

## Installation

Install matching framework and plugin wheels in a regular Python environment. For local development from the repository root:

```bash
python -m pip install -e . -e packages/database
```

Provide an existing PostgreSQL instance. Set `OPSMESH_DATABASE_DSN` in the
service's private environment to a `postgresql+asyncpg://` DSN. Do not put
credentials in a wheel or a world-readable systemd unit. The framework does
not provision or migrate the database.

## Plugin contract

Declare `database = require("database")`. Put
`DatabaseSchema(directory=..., head=...)` in `PluginMetadata.extra["database"]`.
Database discovers declarations from the current loaded-plugin list. Consumers
retain their native SQLAlchemy models and check their own migration head with
`await database.check_migrations(plugin_id)` before using their tables.

`DatabaseAccess()` injects the shared database handle. Each concurrent branch opens its own session or transaction. Operations that must be atomic execute sequentially within one transaction; `AsyncSession` is not a concurrently shared resource.

Each plugin has its own migration version table. Migration remains an explicit
administrative action. In a maintenance process, load the intended plugin set,
enable it for configuration preparation, and call `database.commands.migrate()`
after framework configuration preparation. The [knowledge smoke test](../../tests/integration/knowledge_smoke.py)
shows the sequence against a dedicated test database. The Driver closes the
pool on service shutdown, after the consumers' shutdown hooks.

Migration refuses an unconverted shared `alembic_version` history. Use a
dedicated database for a fresh deployment, or plan and verify an explicit
history conversion first. A wheel package rename does not change the owner
or replay an already applied revision.

## License

MIT; see the package's included license files and third-party notices.
