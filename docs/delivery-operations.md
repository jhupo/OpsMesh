# Delivery operations

Release status (2026-09-20): `v0.1.0rc14` targets database revision
`0102_platform_admin_credentials`, connector protocol 2 and Linux amd64. The tag-triggered release gate is
the source of truth for publication and managed Compose/systemd acceptance. This remains a pre-1.0
release; validate your own ingress, configuration and off-host disaster-recovery policy before use.

## Supported topology and prerequisites

One Linux amd64 installation, one local Postgres 16 database, Redis, and local artifact storage.
The initial managed updater supports this single-host maintenance-window topology. It does not
advertise rolling upgrades, multi-host coordination, ARM64, remote S3 snapshot restoration or
Postgres major upgrades. Such deployments must not use its local backup/restore path.

Install Docker Engine and Compose v2, PostgreSQL 16 client tools (`pg_dump`/`pg_restore`), and
systemd. The backup role needs permission to create temporary databases for restore verification.
The public-release installer does not require GitHub CLI or a GitHub login. If private registry
credentials are required, keep them in the host credential store, never a request body or the
application `.env`. Configure a reverse proxy with TLS to the API's loopback port. No frontend is
installed.

Compose is the default application deployment. Systemd mode uses pre-provisioned Postgres/Redis
and the same updater/state machine with a different deployment implementation. Docker is required
for managed runtime workloads in both modes. Only the worker has Docker authority; API and Agent
runtime containers must not receive its socket.

The host updater is privileged because it manages services and restoration. Install it only on a
trusted dedicated control-plane host. Its root-owned environment is separate from both application
services and workspace runtimes. The updater's own protocol changes require an explicit operator
maintenance procedure; an application update does not hot-replace the executing updater.

## Release procedure

1. Set the same canonical version in root, operator and runtime `pyproject.toml`, regenerate
   `uv.lock`, and review `release-policy.json`. Declare supported source database revisions and
   revisions on which this application may safely run after rollback. Do not infer these from
   version numbers. Canonical prerelease tags use `v0.2.0rc1`.
2. Commit and push to master, then create and push the canonical tag for that commit.
3. **Release Publish** automatically runs the read-only **Release Gate** from the same commit:
   tag/version identity, master ancestry, Ruff, strict mypy, full pytest, real PostgreSQL migrations,
   package builds and a clean operator installation. The validated packages are retained as an
   immutable run artifact. A candidate job builds both images once, with SBOM/provenance, under
   run/attempt-specific candidate tags and probes their exact digests. Native runners also build
   five CLI archives and a Linux amd64 self-contained server archive from the validated wheels.
   Relocation, CLI HTTP requests and server migration/readiness in a Python-free container are
   mandatory checks. Only successful validation enables the staging job, which signs the exact
   package and image digests and uploads the complete asset set to a non-public draft Release.
   The same workflow then runs **Managed Delivery Acceptance** on separate disposable Compose
   and systemd hosts against those exact draft bytes. The CI-only release token is passed as an
   HTTP credential and never makes `gh` a host prerequisite. Its explicitly selected
   previous-release baseline must remain schema-compatible
   with `release-policy.json`; do not replace it with a mutable latest-version lookup. It uses the
   staged native CLI, not the checkout installer, and tests managed installation, approved
   cross-version changes, a killed updater, an occupied API port, explicit resume/rollback, and
   acknowledged database/filesystem restoration. Only after both deployment jobs pass does the
   final job promote the already-tested image digests, publish the draft, download all assets through
   the public release path, check their sizes/hashes, and verify every package and image attestation
   against the repository, workflow, tag and commit. No manual workflow dispatch or previously
   successful workflow run is required. Managed acceptance can also be dispatched against an
   existing immutable public release.
4. Operators discover a version through `update check`; this does not approve or install it.

The workflows use minimal job permissions and pinned Action SHAs. No mutable `latest` reference
is used for production deployment. An incomplete publication is not an installable release. If a
publish job fails, use GitHub's **Re-run failed jobs** to reuse its successful package/candidate
jobs. Existing draft assets must match local size and SHA-256 before missing assets are uploaded;
conflicting or unverifiable bytes fail closed, never use overwrite upload. A matching complete
public release is a no-op. Rebuilding candidates is not a substitute for retrying publication:
different bytes require a new version. Do not move an existing tag. Candidate image tags are not
installable releases and do not participate in update discovery. Redacted release-test diagnostics
are retained for 14 days even when tests fail.

## Install

The release-provided bootstrap script installs missing Ubuntu/Debian prerequisites, downloads the
native CLI and checksum list from the fixed repository release, verifies the archive, and invokes
the managed installer. It never clones or builds source. For Linux amd64:

```sh
TAG=v0.1.0rc14
curl -fsSL "https://github.com/jhupo/OpsMesh/releases/download/${TAG}/install.sh" | \
  sudo sh -s -- --version "${TAG}" --origin https://opsmesh.example.com
```

The script accepts `--repository`, `--root`, and `--mode compose|systemd`. Version and HTTPS origin
are mandatory so a mutable latest release or guessed public address is never installed silently.
Automatic prerequisite installation currently supports Ubuntu and Debian; other Linux amd64 hosts
must prepare the documented commands and run the native CLI directly.

Keep the CLI executable and its bundled libraries together. Python developer wheels remain
available but are not the standalone installation route. The server archive contains its own
CPython and locked production dependencies; installation does not resolve packages online.

The administrator must make the checksummed `opsmesh` and PostgreSQL binaries available to
sudo/systemd; sudo may reset PATH. For systemd application mode add
`--mode systemd` and provide `/opt/opsmesh/.env` for the pre-provisioned database and Redis first.
Use dedicated `opsmesh-api` (UID 10001), `opsmesh-worker` (UID 10002), group `opsmesh` (GID 10001).
The installer rejects identity collisions instead of changing unrelated accounts.

Installer-generated application secrets are random and are never printed. The one-time `superadmin`
password is the deliberate exception: the installer prints it after readiness so the operator can
store it in a password manager; it is not written to `.env` or installation state. Existing
configuration is preserved. New installations explicitly leave
OTLP export disabled until a collector is configured; application logs still use structured output.
To operate the existing monitoring bundle, follow the observability instructions and configure a
reachable secured collector for container clients; container loopback is not host loopback.

```text
/opt/opsmesh/
  .env                       persistent credentials and application configuration
  installation.json          root-owned topology configuration
  installation-status.json   installation recovery marker
  current -> releases/v…     selected immutable release
  releases/                  checksummed bundles, deployment files and release manifests
  updater/                   independent copy of the self-contained server runtime
  downloads/                 bounded downloaded release assets
  updates/<job-id>.json       fsynced execution checkpoints
  backups/<backup-id>/        database, storage and configuration snapshots (sensitive)
  data/storage/              workspace files and artifacts
```

The installer keeps the native operator at `/opt/opsmesh/operator` and exposes it as
`/usr/local/bin/opsmesh`. To rotate the platform administrator password later:

```sh
sudo opsmesh --root /opt/opsmesh admin reset-password
```

After an interrupted first install, rerun the same install/version/mode to resume. A different
version or a completed installation cannot be overwritten this way. Keep backups on protected
storage and copy them off-host according to your disaster-recovery policy; they contain secrets.

## Plan, inspect and apply

Use the existing platform admin token, not a workspace/user/Agent token. Pass it through
`OPSMESH_PLATFORM_ADMIN_TOKEN`. Remote CLI administration requires HTTPS.

```sh
opsmesh update check
opsmesh update plan --version v0.2.0 --idempotency-key upgrade-2026-09
opsmesh update status <plan-id>
opsmesh update apply --plan <plan-id> --fingerprint <plan-sha256>
opsmesh update status <plan-id>
opsmesh logs --service updater
```

Plan creation returns `planning`, not a fabricated success. The host validates/stages the release
and changes it to `ready`. Inspect its old/new releases, database revision, one-hour expiry,
configuration fingerprint and maintenance/data-loss declarations before approval. Approval is
bound to that exact fingerprint; a changed installation requires a new plan. Reusing an idempotency
key for different intent is rejected. Only one active plan/update/recovery may own an installation.
An unstarted plan can be cancelled with `opsmesh update cancel --plan <id>`.

Runtime images from retained releases remain allowed and are not garbage-collected. Existing
runtime templates and run bindings are not rewritten; select the new attested runtime digest when
creating new templates. Connector protocol changes unsupported by this updater fail preflight.

## Backup and recovery

`opsmesh backup create` creates an approval-required maintenance plan for the installed release.
Approve it like an update. `opsmesh backup verify <backup-id> --restore-check` checks hashes, archive
paths and performs a restore into a generated scratch database, which it then removes.

If API is offline, host status remains available:

```sh
sudo opsmesh --root /opt/opsmesh update status <plan-id> --local
sudo systemctl stop opsmesh-updater
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy resume
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy rollback
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy restore --ack-data-loss
sudo systemctl start opsmesh-updater
```

Do not run all recovery commands: select the appropriate one after inspecting the checkpoint.
Stop the idle/recovery-required updater service before the selected local recovery command so its
polling does not compete for the host lock; restart it after successful recovery. Do not kill an
actively executing production update merely because the CLI has not returned yet.
Resume checks that the database is at the approved source or target revision. Application rollback
checks that the previous release explicitly supports the live database revision. Restore uses the
verified pre-upgrade backup and discards database writes since its timestamp; it preserves the
post-backup storage directory under `data/before-restore-*` for manual salvage. Database restoration
requires PostgreSQL's administrative database to remain reachable. A destroyed host/database
cluster needs disaster recovery, not an online update retry.

Recovery validates the strategy, backup requirement, journal/job identity and supported schema
before stopping application services. A durable terminal journal is reconciled into Postgres
without replaying migration, switching, or restoring a database. If the target schema is already
present, resume does not rerun Alembic. Successful rollback/restoration records the previous release,
clears maintenance and releases the active update slot in one database transaction. Release files
remain root-owned and service-readable; restored local storage remains writable by both service
identities through the reserved shared group.

If no verified backup checkpoint exists, resume is allowed only while the database is still at the
approved source revision; it first creates and verifies a new backup. The updater does not
force-kill long Agent tasks to meet a maintenance deadline.
It also does not replay interrupted model/tool calls. Pending approvals and durable queues remain
product state, independent of updater checkpoints.

New API surface: `/api/v1/admin/system/updates/plans`, `/{id}`, `/{id}/events`, `/{id}/apply`, and
`/{id}/cancel`. PID-returning update/restart/rollback endpoints and the old shell updater are gone.
