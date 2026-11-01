# 0002: indexed runtime record projections

Only `runtime_runs` and `runtime_stream` advance to table schema version 2. Other
registered tables retain their own versions. The versioned JSON aggregate remains
the authoritative data; query columns are projections updated in the same transaction
as the aggregate, revision and Outbox facts.

## Query ownership

`modules/runtime/internal/records.py` owns `RunRecordStore` and `RunStreamStore`.
Run projections cover status, principal, session and worker lease expiry. Stream
projections cover Run ID and sequence. Run selection and stream replay apply filters,
stable ordering and bounded pagination in SQL. Background traversals use a keyset
cursor so changing a selected Run's status does not skip the next page.

## Upgrade

1. Stop gateway and worker processes, including isolated Run workers. Do not mix old
   and new writers: old binaries do not maintain the new query projections.
2. Back up the database and configured file/blob storage.
3. Install the new package and run `opsmesh migrate` with the deployment configuration.
4. Run `opsmesh check` before restarting gateway and worker services.

The migration validates both the recorded version-1 fingerprint and the actual old
table structure, adds nullable projection columns, backfills existing JSON values,
creates indexes and advances the version catalog. IDs, JSON, revisions and timestamps
are preserved. Running the migration again verifies the installed result.

Startup verification refuses a version-1 runtime table and never upgrades it implicitly.
Unknown versions or altered tables fail validation rather than receiving a guessed
conversion. PostgreSQL uses an advisory transaction lock; SQLite uses `BEGIN IMMEDIATE`.
SQLite DDL rollback behavior depends on its driver, so restore the pre-upgrade backup
if an interrupted migration leaves a partial table; do not edit the version catalog.

There is no destructive downgrade command. Rolling back requires stopping all writers,
restoring the database backup and restoring the matching application package.

## Regression evidence

`tests/runtime/test_indexed_records.py` covers old-table upgrades, projection consistency,
CAS failure rollback, bounded SQL queries, keyset traversal and stream sequence replay.
`tests/infrastructure/test_postgresql.py` repeats the populated-table upgrade against an
explicit temporary PostgreSQL instance. Neither test invokes a model API.
