# Release delivery implementation

Status: in progress. A green unit test is not deployment evidence.

## Acceptance and sequence

| Stage | Scope | Status |
| --- | --- | --- |
| 1 | master CI, pre-tag release gate, locked image builds, packages, GHCR, signed manifest | In progress |
| 2 | lightweight CLI, production Compose/systemd deployment, install and diagnostics | Pending |
| 3 | durable platform update jobs, independent host updater, maintenance and audit | Pending |
| 4 | verified backup, interruption recovery, constrained rollback and Linux end-to-end gate | Pending |

Each completed functional point is committed separately. No production tag or live deployment is
created as a side effect of implementation. Linux installation and update evidence must be recorded
before claiming the delivery system is complete.

## Boundaries

- Postgres owns platform update intent, approval and audit. The updater's fsynced host journal is a
  recovery checkpoint while the application/database is offline; it is reconciled to Postgres.
- API processes never obtain Docker or systemd authority and never spawn update commands.
- A host-managed updater accepts only validated release identities and fixed operations. Remote
  clients cannot supply commands, arbitrary URLs, filesystem paths or service names.
- Deployment policy uses typed product contracts. Docker SDK, Compose, uv, Alembic, GitHub CLI and
  official build/attestation Actions own their existing infrastructure capabilities.
- The operator CLI is a separate lightweight package: no model SDK or backend dependency tree.
- Compose is the primary packaged deployment; systemd uses the same release contract and update
  engine, not a second shell updater. No legacy endpoint shims or silent deployment fallbacks.
- Updates have maintenance windows. New work is held; active side effects are never replayed.
- Configuration, secrets, data and runtime files are outside immutable release directories.
- App rollback is allowed only with an explicitly supported database revision. Database restoration
  requires separate operator approval, with the potential data-loss window displayed.
- Runtime images are pinned by digest and retained while active runs reference them. Connector
  protocol requirements are checked, not silently upgraded on user-owned machines.

## Supply chain

The release tag, package versions, commit, OCI digests and migration head form one release identity.
The release manifest is published only after all required assets pass their checks. GitHub build
attestations authenticate provenance, not just file integrity. A checksum downloaded next to an
archive is not a signature. The updater verifies the repository/workflow identity using the
official GitHub CLI. Stable channels exclude prereleases; production pins digests, never latest.

Use official Docker build Actions (https://docs.docker.com/build/ci/github-actions/) and GitHub
attestations (https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations).
Do not add a homegrown signer, container orchestrator or general workflow framework. Python's
argparse is adequate for the initial CLI; existing httpx/Pydantic/SQLAlchemy cover transport and
contracts. A separate giant CLI framework is not needed.

## Validation evidence

- Before changes: release versioning + deployment asset tests: 29 passed, 1 failed. Existing
  migration 0063 performs an online Python backfill and cannot render the entire chain offline.
- Current workstation: Windows, Python 3.13 virtualenv; Docker is not installed. uv is available
  inside `.venv/Scripts`. Real container and systemd evidence requires the Linux CI environment.
- Stage 1 local evidence: 36 focused tests pass; all three distributions build as wheel and sdist;
  frozen lock validates. Migration 0063 now explicitly rejects offline execution before emitting
  partial DDL; its Python canonical JSON backfill still runs online. The release gate uses an actual
  Postgres connection, not an incomplete offline SQL script.
- Normal development and PRs use focused tests. Full pytest is restricted to the pre-tag gate.
