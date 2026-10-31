# Database migrations

`opsmesh migrate` explicitly installs the registered version 1 schema. `opsmesh init`
uses the same migration entry point before creating an administrator. `opsmesh check`,
gateway startup and worker startup verify the installed schema; verification never
creates or changes tables. Configure `OPSMESH_DATABASE_URL` before running these commands.

The executable migration is `src/opsmesh/infrastructure/database/migrations.py`. Each module
registers its tables during application assembly, before migration or verification.
`opsmesh_schema_versions` records each installed table's version and schema fingerprint.
The verifier checks the actual columns, nullability, primary key, indexes and check
constraints in addition to that fingerprint. A new module can add a new version 1 table
only through an explicit migration run.

This release contains the initial migration only. It deliberately refuses existing
unversioned application tables, changed version numbers, changed declared schemas and
structure drift. It does not label `create_all()` as an upgrade path or silently rewrite
the schema-version rows. A future schema change needs a numbered migration that performs
the ALTER/backfill, validates the result and advances its affected table versions in the
same deployment operation. Do not manually edit fingerprints to suppress a mismatch.

Migration runs use a transaction and a PostgreSQL advisory transaction lock; SQLite
uses `BEGIN IMMEDIATE`. Schema changes are expected to run while application workers are
stopped. Back up a populated deployment before an upgrade. The initial installation has
no destructive downgrade command: restoring a backup is an operator action.

See [0001 — initial storage](0001-initial.md) for ownership, delivery guarantees and
the current generic-record storage limits.
