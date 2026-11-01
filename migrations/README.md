# Database migrations

`opsmesh migrate` explicitly installs or upgrades the registered schema. `opsmesh init`
uses the same migration entry point before creating an administrator. `opsmesh check`,
gateway startup and worker startup verify the installed schema; verification never
creates or changes tables. Configure `OPSMESH_DATABASE_URL` before running these commands.

The executable migration is `src/opsmesh/infrastructure/database/migrations.py`. Each module
registers its tables during application assembly, before migration or verification.
`opsmesh_schema_versions` records each installed table's version and schema fingerprint.
The verifier checks the actual columns, nullability, primary key, indexes and check
constraints in addition to that fingerprint. A new module can add a new version 1 table
only through an explicit migration run.

The initial installation is version 1 for ordinary tables. Run records and Run stream
records use version 2, with an explicit version 1 upgrade that adds indexed projections
and backfills existing JSON data. The migrator refuses unversioned application tables,
unknown versions and structure drift. It never rewrites fingerprints to suppress a
mismatch. Future schema changes require an explicit migration.

Migration runs use a transaction and a PostgreSQL advisory transaction lock; SQLite
uses `BEGIN IMMEDIATE`. Schema changes are expected to run while application workers are
stopped. Back up a populated deployment before an upgrade. The initial installation has
no destructive downgrade command: restoring a backup is an operator action.

See [0001 — initial storage](0001-initial.md) for ownership, delivery guarantees and
the generic-record storage design, and [0002 — runtime projections](0002-runtime-projections.md)
for the indexed Run queries and upgrade procedure.
