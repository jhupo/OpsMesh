# Release delivery implementation

Status: in progress. A green unit test is not deployment evidence.

## Acceptance and sequence

| Stage | Scope | Status |
| --- | --- | --- |
| 1 | master CI, pre-tag release gate, locked image builds, packages, GHCR, signed manifest | Implemented; hosted execution pending |
| 2 | lightweight CLI, production Compose/systemd deployment, install and diagnostics | Implemented; Linux installation acceptance pending |
| 3 | durable platform update jobs, independent host updater, maintenance and audit | Implemented; focused contract tests pass |
| 4 | verified backup, interruption recovery, constrained rollback and Linux integration gate | Implemented locally; real upgrade/restore acceptance pending |

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
- Baseline audit against detached `d264ffb`: mypy reports 721 existing errors. The first new run
  reported 728; the seven additional diagnostics were operator package discovery, corrected with
  an explicit mypy source path and `py.typed`. No type errors are suppressed to publish a release.
- Broader worker/self-hosted checks: 11 failures are reproduced on `d264ffb` (missing authorization
  fixtures and outdated contract assertions). The same 11 failures occur with the delivery change.
- GitHub CLI exists but is not authenticated. No hosted workflow dispatch, release tag or production
  update has been executed. These are validation blockers, not evidence of successful deployment.
- Final focused set: 55 passing tests (delivery contracts, operator security, update state machine,
  admin API, deployment assets). Ruff passes repository-wide. The 16 newly introduced/affected
  operator, update-domain and route modules pass strict mypy. Alembic has one head:
  `0071_platform_delivery`. Wheels and source distributions build for all three packages.
- The Linux integration workflow exercises PostgreSQL migration/locking/WORM and fresh Docker
  startup. A real released-version-to-released-version upgrade, systemd install and database restore
  have NOT been executed on this Windows host. Do not report these acceptance items as complete.
- First hosted delivery run passed focused tests and type checks, but fresh PostgreSQL migration
  failed: historical revision `0061_pending_tool_execution_results` exceeded Alembic's 32-character
  version column. Its identifier and successor reference are shortened together, without aliases.
  A migration-chain regression check now enforces the length limit; all 11 delivery tests pass.
  Hosted migration acceptance must be rerun after this fix. Public API access is rate-limited and
  downloading complete Actions logs requires GitHub authentication.
