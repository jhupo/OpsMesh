# Delivery operations

Implementation is not yet production-accepted: see [release delivery evidence](release-delivery-plan.md).
Backend CI, real Linux backup restoration and rc5 signed publication/download verification pass.
Managed installation and cross-version upgrade acceptance must still pass before delivery is
declared complete.

## Supported topology and prerequisites

One Linux amd64 installation, one local Postgres 16 database, Redis, and local artifact storage.
The initial managed updater supports this single-host maintenance-window topology. It does not
advertise rolling upgrades, multi-host coordination, ARM64, remote S3 snapshot restoration or
Postgres major upgrades. Such deployments must not use its local backup/restore path.

Install Docker Engine and Compose v2, GitHub CLI with attestation support,
PostgreSQL 16 client tools (`pg_dump`/`pg_restore`), and systemd. The backup role needs permission to
create temporary databases for restore verification. Authenticate `gh` for the public repository
and GHCR if necessary; credentials belong to the host updater environment, never a request body.
For unattended authentication use a root-readable `updater.env` (mode 0600), not the shared `.env`;
the latter is delivered to application containers. Never put registry/GitHub credentials there.
Configure a reverse proxy with TLS to the API's loopback port. No frontend is installed.

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
   mandatory checks. Only successful validation
   enables the signing/publishing job, which downloads the same packages and promotes the tested
   image digests without rebuilding. It publishes the draft Release last, then downloads its public
   assets through the operator's real release source, checks their sizes/hashes and verifies every
   package and both image attestations against the repository, workflow, tag and commit. These
   post-publication checks must also pass before the workflow reports success. No manual workflow
   dispatch or previously successful workflow run is required.
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

Download the native CLI archive for your host from the release, and verify before extraction.
For Linux amd64 (replace the tag with your approved version):

```sh
gh attestation verify opsmesh-cli-v0.1.0rc6-linux-amd64.tar.gz --repo jhupo/OpsMesh \
  --signer-workflow jhupo/OpsMesh/.github/workflows/release-publish.yml \
  --source-ref refs/tags/v0.1.0rc6 --deny-self-hosted-runners
mkdir opsmesh-cli
tar -xzf opsmesh-cli-v0.1.0rc6-linux-amd64.tar.gz -C opsmesh-cli
./opsmesh-cli/opsmesh doctor
sudo ./opsmesh-cli/opsmesh --root /opt/opsmesh install --version v0.1.0rc6 --origin https://opsmesh.example.com
```

Keep the CLI executable and its bundled libraries together. Python developer wheels remain
available but are not the standalone installation route. The server archive contains its own
CPython and locked production dependencies; installation does not resolve packages online.

The administrator must make the verified `opsmesh`, `gh` and PostgreSQL binaries available to
sudo/systemd; sudo may reset PATH and authentication environment. For systemd application mode add
`--mode systemd` and provide `/opt/opsmesh/.env` for the pre-provisioned database and Redis first.
Use dedicated `opsmesh-api` (UID 10001), `opsmesh-worker` (UID 10002), group `opsmesh` (GID 10001).
The installer rejects identity collisions instead of changing unrelated accounts.

Installer-generated secrets are random and are never printed. Read/configure them on the host under
administrator control. Existing configuration is preserved. New installations explicitly leave
OTLP export disabled until a collector is configured; application logs still use structured output.
To operate the existing monitoring bundle, follow the observability instructions and configure a
reachable secured collector for container clients; container loopback is not host loopback.

```text
/opt/opsmesh/
  .env                       persistent credentials and application configuration
  installation.json          root-owned topology configuration
  installation-status.json   installation recovery marker
  current -> releases/v…     selected immutable release
  releases/                  verified bundles, deployment files and release manifests
  updater/                   independent copy of the verified self-contained server runtime
  downloads/                 bounded downloaded release assets
  updates/<job-id>.json       fsynced execution checkpoints
  backups/<backup-id>/        database, storage and configuration snapshots (sensitive)
  data/storage/              workspace files and artifacts
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
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy resume
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy rollback
sudo opsmesh --root /opt/opsmesh update recover --plan <plan-id> --strategy restore --ack-data-loss
```

Do not run all recovery commands: select the appropriate one after inspecting the checkpoint.
Resume checks that the database is at the approved source or target revision. Application rollback
checks that the previous release explicitly supports the live database revision. Restore uses the
verified pre-upgrade backup and discards database writes since its timestamp; it preserves the
post-backup storage directory under `data/before-restore-*` for manual salvage. Database restoration
requires PostgreSQL's administrative database to remain reachable. A destroyed host/database
cluster needs disaster recovery, not an online update retry.

If no verified backup checkpoint exists, resume is allowed only while the database is still at the
approved source revision; it first creates and verifies a new backup. The updater does not
force-kill long Agent tasks to meet a maintenance deadline.
It also does not replay interrupted model/tool calls. Pending approvals and durable queues remain
product state, independent of updater checkpoints.

New API surface: `/api/v1/admin/system/updates/plans`, `/{id}`, `/{id}/events`, `/{id}/apply`, and
`/{id}/cancel`. PID-returning update/restart/rollback endpoints and the old shell updater are gone.
