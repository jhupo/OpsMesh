# Self-contained release distributions

The operator subprocess boundary restores the host library search path when running as a frozen
Linux CLI, following [PyInstaller's external-program guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application).
Host `systemctl`, PostgreSQL tools and the separate server runtime must not inherit CLI library
overrides. This does not change the running CLI's own environment or add another command runner.

The release pipeline creates and verifies GitHub build attestations before it reports success.
Host installation deliberately requires neither GitHub CLI nor a GitHub credential. The bootstrap
script downloads from the configured repository/tag, validates manifest identity and
platform/protocol fields, check package SHA-256 values, and retain digest-pinned images.

## Decision and scope

Production deployment has exactly two modes: Docker (`--mode compose`) and native Linux
installation (`--mode systemd`). Both use the same `opsmesh-server` commands. Source
checkouts and Python developer packages are development inputs, not a third production mode.

The native operator CLI uses PyInstaller onedir builds on each native runner (Linux amd64/arm64,
Windows amd64, macOS amd64/arm64). It bundles Python and operator host dependencies, but not the
backend or model SDK dependency trees. Host administration still requires Linux; desktop CLIs
provide remote administration. Archives retain dependency metadata and licensing information.
The native test runners are Ubuntu 22.04 amd64, Ubuntu 24.04 arm64, Windows Server 2022 amd64,
macOS 15 Intel and macOS 14 Apple Silicon. Older OS versions are not claimed as tested. GitHub
attestations authenticate every archive; these are not Windows Authenticode or Apple notarization
certificates. Local OS security prompts may therefore still apply.

The server distribution is a Linux amd64 PyInstaller onedir executable. It contains the
runtime and native libraries needed by the server, migrations and deployment assets;
it does not ship a complete `python/` installation or require system Python, pip or uv.
It is not a static machine-code build. Updates replace one immutable executable directory.
PostgreSQL, Redis and Docker remain infrastructure dependencies. Compose mode owns their declared services; direct systemd mode requires pre-provisioned PostgreSQL and Redis. Neither distribution contains a frontend.

PyInstaller is build-only. Native dependencies and dynamically loaded modules are collected
from locked wheels; Temporal sandbox imports retain necessary source files. The release gate
must exercise the frozen directory before upload. Plugin code is installed separately in the
persistent host plugin directory. API, Worker and Gateway watch published plugin generations
and reload them; service restart is not required for normal package activation.

References: https://pyinstaller.org/en/stable/operating-mode.html and
https://docs.astral.sh/uv/guides/install-python/ .

## Runtime contract

`opsmesh-server version`, `check`, `api`, `worker`, `gateway`, `migrate`, `init-admin`,
`bootstrap-admin`, `reset-admin-password` and `cleanup-login-sessions` use the shared
server command implementation in either deployment mode.
Native installations resolve paths relative to the executable directory; Docker uses `/app`.
`--directory` explicitly selects another working directory. Configuration is read from that
directory's `.env` and process environment. No secrets enter archives.
Systemd uses the same launcher. Host release operations run only when explicitly invoked by an
administrator and are independent of the application API lifecycle.

Each build is extracted into a different directory before execution. Native CLI checks run with
an isolated PATH and exercise both version and an actual loopback administrative HTTP request.
Server acceptance uses an otherwise Python-free container, real PostgreSQL/Redis, migrations,
API/worker readiness. Only those tested archives enter signing/publication.
The release manifest and checksums enumerate native archives as well as Python developer packages.

Source code is packaged from `src/opsmesh` under the `opsmesh` import name. Server archives copy
Alembic assets into `migrations/`; native launchers import `opsmesh.bootstrap.cli`.
Build with `uv build --all-packages`
to produce source distributions and then wheels from those distributions. Direct wheel builds
can reuse a stale setuptools `build/lib` directory; the release gate compares wheel contents
with current source before accepting the artifacts.

## Required release acceptance configuration

CI runs current tests against disposable PostgreSQL, Redis, Temporal and Docker.
Release validation additionally requires the native SDK model/tool/session smoke and
`scripts/check_runtime_image.py` for a Docker sandbox and MCP stdio round trip.
These focused checks do not certify the complete OpsMesh Agent/Workflow lifecycle;
the retired provider/revision acceptance runner has been removed. Coverage gaps are
listed in [tests/README.md](../tests/README.md).
Ordinary pull requests do not receive the external model credential.

Configure the repository secrets `OPSMESH_TEST_MODEL_API_KEY`,
`OPSMESH_TEST_MODEL_BASE_URL` and `OPSMESH_TEST_MODEL_NAME` for an isolated acceptance upstream.
`OPSMESH_TEST_MODEL_REASONING_EFFORT` selects the reasoning effort (defaults to `medium`).
The selected model must support the Responses API and tool calls. Keep credentials out of
repository files. The reusable release workflow passes the secret only to the native SDK
smoke step; release packaging and publication jobs do not receive it.

Run local checks only when explicitly requested. Use the `OPSMESH_TEST_*` variables declared
by the selected tests and a disposable database: fixtures migrate to head and truncate
current and known retired feature tables between scenarios. Redacted CI diagnostics are
recorded under `reports/`. A skipped external check is unverified.

The default container Worker and compute image both run as UID/GID 10001 so bind-mounted
working directories remain writable without world-write permissions. Systemd uses separate
service UIDs, shared GID 10001 and UMask 0007. Custom images must preserve the effective
directory permissions. Source-based CI builds the compute image using its runner's
UID/GID through `SANDBOX_UID` and `SANDBOX_GID`. When testing through a Docker socket mounted
inside a container, mount the checkout at the same absolute path on both hosts; acceptance
uses `.tmp/product-acceptance` there so Docker can resolve each working directory.

Production Compose binds `${OPSMESH_ROOT}/data/storage` at that identical absolute path in
API and Worker containers. Local Compose uses `OPSMESH_DOCKER_STORAGE_ROOT` the same way;
its one-shot `storage-init` sets UID/GID 10001 and mode 2770 before API startup. A named
volume or a different container-side path cannot supply native Docker bind mounts correctly.
