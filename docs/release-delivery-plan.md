# Release delivery implementation

Status: in progress. A green unit test is not deployment evidence.

## rc6 standalone publication acceptance (2026-09-10)

- [Release Publish 34375776121](https://github.com/jhupo/OpsMesh/actions/runs/34375776121)
  passed on its first attempt for `v0.1.0rc6`, commit `e95aedf`. Full tests: **1174 passed,
  9 skipped**; Ruff, strict mypy, real migration/schema checks and clean wheel installation pass.
- All five native CLI jobs passed on Linux amd64/arm64, Windows amd64 and macOS amd64/arm64.
  Each archive was relocated before version/doctor and real administrative HTTP request checks
  with system Python removed from PATH. The CLI dependency environment excludes backend/model SDKs.
- The Linux amd64 server archive passed in a Python-free Ubuntu container: bundled SDK/driver/
  timezone imports, real PostgreSQL migrations/schema comparison, Worker, updater, API readiness,
  shell syntax and the bundled Alertmanager renderer. Both Docker image probes and production
  Compose acceptance passed independently.
- [Release v0.1.0rc6](https://github.com/jhupo/OpsMesh/releases/tag/v0.1.0rc6) contains 14 assets:
  five native CLI archives, one self-contained server archive, six wheel/sdist developer packages,
  `checksums.txt` and the signed release manifest. The server archive is 216432032 bytes; native
  CLI archives are approximately 21-29 MiB. The obsolete source-only server bundle is not emitted.
- The final workflow step downloaded every public asset through the operator release source,
  checked sizes/SHA-256 and verified package/archive/OCI provenance against the repository,
  workflow, tag and commit. Anonymous version-tag GHCR requests also returned 200 with the same
  signed digests: backend `sha256:f2121b9ffffe945379e7c0d195d6207d97dd17f6133b1566fe3f3e5f44e7492f`,
  runtime `sha256:4fb97ede44776aa343dc9a29bbdfed22e2f4f67f5c6abaf5c709da758289aaae`.
- This completes the standalone artifact/publication correction. Server support remains Linux
  amd64; desktop CLI availability does not imply desktop server support. Full managed systemd
  install and cross-version interrupted-updater recovery remain separately unaccepted.
- Workstation post-publication verification also passed: the real operator release source fetched
  the signed manifest and Windows archive, checked SHA-256/provenance, then the public archive
  passed relocated version/doctor and HTTP administration checks without Python on PATH. An initial
  stalled gh download was stopped; no partial file was executed or substituted for verification.

## Standalone delivery correction

The rc5 publication checkpoint below validated wheels, source bundles and images, not standalone
executables. Native ready-to-run archives were missing from that acceptance boundary. The current
change replaces source-based host installation with a bundled CPython runtime, adds five native
CLI archives, signed checksums and mandatory relocated-artifact execution before publication.
Windows CLI relocation and a real administrative HTTP request passed locally before publication.
The following checkpoints record repairs; final hosted/public evidence is in the rc6 section above.

- Native CLI matrix run `34373885167` passed all five platforms; its server job exposed uv alias
  discovery, subsequently fixed through `uv python find`. Run `34374107928` again passed all five
  native CLIs and exposed mixed diagnostic/JSON output in the server check, now regression-tested.
- Server acceptance `34375162276` passed in a Python-free Ubuntu container: packaged SDK/native
  imports and timezone data, all migrations and schema comparison, Worker, a real updater tick and
  API readiness. The safety rejection of an overly broad test root was fixed in the test fixture,
  not by weakening installation validation.
- Strict mypy passes 1005 application source files. Runtime/operator, publication and deployment
  asset regression checks pass. Operational scripts now ship in the runtime and use its interpreter;
  obsolete source-venv installation instructions and duplicated manual unit templates were removed.
- Final tag publication, native archive signatures and public download verification subsequently
  passed in rc6; these checkpoints alone were not treated as completion.

## rc5 final publication acceptance (2026-09-09)

- [Release Publish 34368777720](https://github.com/jhupo/OpsMesh/actions/runs/34368777720)
  succeeded on its first attempt for commit `d045b5d` / tag `v0.1.0rc5`. The gate reports
  **1168 passed, 9 skipped**; Ruff, strict mypy, real PostgreSQL migrations/schema comparison,
  all package builds and clean-environment operator installation passed.
- Both candidate images passed exact-digest startup probes and production Compose migrations,
  API/worker startup and readiness. The first-creation publication path succeeded without a
  retry, uploaded the eight release assets, promoted the tested image digests and published
  [v0.1.0rc5](https://github.com/jhupo/OpsMesh/releases/tag/v0.1.0rc5).
- The final workflow step downloaded the public manifest and all seven packages, checked size
  and SHA-256, and verified GitHub provenance for the packages and both OCI images, enforcing
  repository, signing workflow, source tag/commit and hosted runners. This is actual published
  artifact acceptance, not only a successful upload or mocked signature check.
- Anonymous GHCR version-tag requests return HTTP 200 for both images. Backend digest:
  `sha256:3a2dbaac6dfb34eb18d4dbc24f539b924262c27bb922c1a367e4458347692132`;
  runtime digest: `sha256:0bdd548ac1e1420e6d0908bd311e1df078d09c21cfe00eebba5840939c4ee081`.
- rc4 also passed signed public download verification; its downloaded operator wheel installed
  in a clean workstation environment and `opsmesh version` returned `0.1.0rc4`. No production
  installation was changed. Stage 1 is accepted; systemd/managed install and cross-version
  upgrade/interruption recovery remain unaccepted and are not implied by this release result.

## rc4 publication checkpoint

- [Release run 34366081003](https://github.com/jhupo/OpsMesh/actions/runs/34366081003)
  passed the complete gate, exact-digest image probes and fresh production Compose acceptance.
  Its first publication attempt created a draft but immediately failed to find it in list results.
  Retrying only the failed publication job reused the tested packages/images and published all
  eight assets successfully. The creation path now consumes the official API creation response
  directly instead of rediscovering the new ID through a potentially stale list.
- The operator's actual public-download path verified the rc4 manifest signature against the
  repository, workflow, tag and commit; all seven package downloads matched declared size/SHA256.
  Publication now also runs public download and provenance checks for every package and both OCI
  images before the workflow may report success. The new creation path requires a fresh tag run.
- [Delivery Integration 34365800771](https://github.com/jhupo/OpsMesh/actions/runs/34365800771)
  passed real PostgreSQL schema-drift checks, the 0071/0072 downgrade/upgrade roundtrip, Compose
  startup and backup restoration. Systemd installation and managed cross-version recovery remain
  separate outstanding acceptance items.

## rc3 verification checkpoint

- Run `34363744943` passes full pytest: 1165 passed, 8 skipped. It then rejects ORM/schema drift
  through `alembic check`; no packages or images were published.
- ORM metadata now retains the deployed timezone-aware fields, named email uniqueness and existing
  task/runtime/PostgreSQL memory indexes. Migration `0072_release_schema` aligns marketplace source
  nullability, JSONB memory snapshots and quota/reservation timestamps (explicit UTC conversion).
  Invalid existing rows fail migration rather than being deleted or fabricated.
- Delivery Integration now checks metadata drift and the 0071/0072 downgrade/upgrade roundtrip in
  disposable PostgreSQL. This validates the migration before another immutable release attempt.

## Publication repair checkpoint (2026-09-09)

- `v0.1.0rc1` run `34352635366` failed during full pytest; publication was skipped. Its diagnostic
  wrapper incorrectly replaced the entire output with `[redacted]`. Fragment redaction now
  preserves failure context and the release gate retains a redacted test report for 14 days.
- Packages are built once and transferred as a run artifact. Candidate images are built once under
  run/attempt-specific tags, tested by digest with production Compose migrations and readiness,
  then promoted without rebuilding. Candidate tags do not participate in release discovery.
- Publication verifies existing draft assets by size/hash, uploads only missing files, verifies
  inventory, promotes image digests and publishes the draft last. Matching public releases are
  read-only no-ops; conflicting bytes fail closed. Retry failed jobs, not a fresh build of an
  existing version. These changes still require hosted release acceptance.
- `rc2` run `34355450949` retained diagnostics successfully: 1160 passed, 8 skipped, 5 failed.
  Four failures were environment-dependent test fixtures (default settings and heartbeat token);
  one was a Python 3.12 manager-diagnostics comprehension/name-shadowing error. Commits `d151dc0`
  and `cbb4beb` repair them. All five affected tests pass locally, also with the release runner's
  environment/token overrides. The previous broad local run overlapped workflow edits and is not
  clean immutable-checkout acceptance evidence.
- Signed publication/installation, systemd and cross-version recovery acceptance remain outstanding.
  Both approved rc tags retain their original failed commits; fixes are on master. A new authorized
  release version is needed to run the repaired publication chain, not a force-moved existing tag.
  Do not interpret this workflow repair as completion of all delivery stages.

## Acceptance and sequence

| Stage | Scope | Status |
| --- | --- | --- |
| 1 | master CI, tag-triggered release gate, locked image builds, native archives, packages, GHCR, signed manifest | Accepted: rc6 native matrix, self-contained server, public downloads and provenance pass |
| 2 | lightweight CLI, production Compose/systemd deployment, install and diagnostics | Implemented; Linux installation acceptance pending |
| 3 | durable platform update jobs, independent host updater, maintenance and audit | Implemented; focused contract tests pass |
| 4 | verified backup, interruption recovery, constrained rollback and Linux integration gate | Real Linux/PostgreSQL backup restoration passes; integrated upgrade/recovery acceptance pending |

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
- Normal development and PRs use focused tests. Full pytest is restricted to the tag-triggered gate.
- Baseline audit against detached `d264ffb`: mypy reports 721 existing errors. The first new run
  reported 728; the seven additional diagnostics were operator package discovery, corrected with
  an explicit mypy source path and `py.typed`. The baseline remains a blocking release gate.
- Broader worker/self-hosted checks: 11 failures are reproduced on `d264ffb` (missing authorization
  fixtures and outdated contract assertions). The same 11 failures occur with the delivery change.
- GitHub CLI exists but is not authenticated. Public Actions status can be inspected in the browser
  without CLI login, and authenticated Git pushes already trigger CI. CLI login is not a blocker
  for this repair. No release tag or production update has been executed.
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
- The next hosted run exposed oversized explicit foreign-key names in migration 0063; scanning
  the migration chain found the same defect in 0064. Both upgrade and downgrade now use Alembic
  `op.f` so PostgreSQL names match SQLAlchemy's deterministic naming convention. All 12 delivery
  tests pass, including a chain-wide explicit-identifier regression check.
- Hosted Delivery Integration run `34332895713` on commit `9acf57e` succeeded in 2m 7s:
  https://github.com/jhupo/OpsMesh/actions/runs/34332895713 . This verifies actual PostgreSQL
  migrations, update locking/append-only audit contracts, fresh Docker Compose build/start/readiness
  and the runtime image probe. It does not verify signed release publication, cross-version managed
  upgrades, systemd installation, or backup restoration. Those remain outstanding acceptance work.

## Baseline repair checkpoint (2026-09-09)

- At commit `f9112a6`, strict mypy passes all 1004 source files (zero errors), down from the
  original 721. Ruff passes repository-wide. Both commands were rerun without module filters or
  relaxed settings. Hosted Backend CI run `34345716568` also passed dependency sync, Ruff and strict
  type checks but failed changed-area tests. After the repairs below, Backend CI
  [run 34348069051](https://github.com/jhupo/OpsMesh/actions/runs/34348069051) on `dc1a7b2`
  completed successfully, including the changed-area tests.
- Repairs replace unstructured cross-module contracts with protocols/TypedDicts, preserve SQL
  workspace scope, use native Redis/S3 SDK contracts, and fix working-memory list redaction and
  cross-workspace capacity batches. Each functional change has a separate commit and focused tests.
- Subsequent repairs cover the OpenAI session protocol and native SDK tool provenance, Claude SDK
  hook/store contracts, fail-closed approval decisions, task/Run lifecycle scope, runtime event
  redaction, typed team scheduling, reporting trees, operational summaries and operator audit
  results. Regression tests exercise partial finalization filtering, runtime startup before
  scheduling, quota denial, approval expiration/cancellation scope and message-only participants.
- Old synchronous-runtime tests now run worker startup before expecting authorized runs. Quota
  fixtures use an online runtime or workspace-scoped placement as appropriate; authorization gates
  remain enabled. Focused tests are run per repaired behavior, not a full release suite.
- Nineteen `prop-decorator` annotations apply only to Pydantic computed properties, following its
  [documented mypy limitation](https://pydantic.dev/docs/validation/latest/api/pydantic/fields/).
  No global type-check relaxation or compatibility implementation was added.
- Backend CI now reuses the delivery command wrapper to publish bounded, redacted failure
  annotations. Public status inspection does not require downloading authenticated full logs.
  The wrapper preserves merged stdout/stderr ordering and the test selector propagates pytest's
  exit status, so a wrapper traceback cannot hide the actual failed-test summary.
- Worker fixtures now create positive authorization snapshots through the production snapshot
  service. Missing/obsolete snapshots, scope violations and tampering remain rejected. Cost tests
  supply typed usage; marketplace and mailbox assertions follow the current memory and catalog
  contracts. A bounded reproduction of the failing changed-area set and focused failure reruns
  were used; this was not a full pre-release test suite.
- Revoked team membership now records `capability_authorization_blocked` on the waiting step
  without rolling back the previously completed step. Frozen planning assignments do not grant
  permission to execute as a revoked member; tests cover both active and revoked membership.
- Previously recorded WorkerRunner and self-hosted failures were revalidated and repaired locally:
  authorized queue fixtures, queue-producer/worker-consumer trace ancestry, async MCP adapters,
  current runtime resource grants and project-limit trust summaries. Final hosted Backend CI
  [run 34348886244](https://github.com/jhupo/OpsMesh/actions/runs/34348886244) on `d09ce2d`
  completed successfully, including strict type checks and the selected Worker/self-hosted tests.
- Hosted Delivery Integration run `34340457352` on `97e7dab` also succeeded (2m 6s), confirming the
  previously recorded migration and fresh-container acceptance. This is not cross-version update
  or signed-publication evidence.
- Signed release assets, cross-version upgrade and systemd installation remain unaccepted; do not
  describe the complete delivery system as finished on this checkpoint alone.

## Real backup recovery acceptance (2026-09-09)

- Commit `8341378` adds an opt-in Linux administrator acceptance test using actual `pg_dump` and
  `pg_restore`, not a mocked backup implementation. It creates and removes only a randomly named
  disposable database, never the provided CI database.
- [Delivery Integration 34349940656](https://github.com/jhupo/OpsMesh/actions/runs/34349940656)
  passed the real backup/restoration step, PostgreSQL migration checks, fresh Compose deployment
  and runtime image probe. Restored database rows, workspace files, configuration contents,
  ownership and permissions are asserted. Post-backup files remain available for salvage;
  missing data-loss acknowledgement and tampered dumps are rejected before restoration.
- Fixed restoration replacing a systemd-readable configuration with a root-only file: atomic
  replacement now retains the existing configuration owner, group and mode.
- This proves backup-store restoration, not the entire interrupted-updater recovery workflow.
  Final acceptance still needs two approved published versions, their attested assets, and actual
  Compose/systemd install and upgrade runs. Proposed acceptance releases are `v0.1.0rc1` and
  `v0.1.0rc2`; publication of both prereleases and GHCR images is now approved. Production changes
  remain out of scope. Signed installation and cross-version recovery acceptance are still pending.
