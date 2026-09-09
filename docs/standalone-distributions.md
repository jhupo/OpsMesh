# Self-contained release distributions

Accepted in [v0.1.0rc6](https://github.com/jhupo/OpsMesh/releases/tag/v0.1.0rc6):
[native matrix, server startup and public provenance verification](https://github.com/jhupo/OpsMesh/actions/runs/34375776121).

## Decision and scope

The native operator CLI uses PyInstaller onedir builds on each native runner (Linux amd64/arm64,
Windows amd64, macOS amd64/arm64). It bundles Python and operator host dependencies, but not the
backend or model SDK dependency trees. Host administration still requires Linux; desktop CLIs
provide remote administration. Archives retain dependency metadata and licensing information.
The native test runners are Ubuntu 22.04 amd64, Ubuntu 24.04 arm64, Windows Server 2022 amd64,
macOS 15 Intel and macOS 14 Apple Silicon. Older OS versions are not claimed as tested. GitHub
attestations authenticate every archive; these are not Windows Authenticode or Apple notarization
certificates. Local OS security prompts may therefore still apply.

The server distribution supports the existing Linux amd64/glibc deployment. It contains CPython
3.12.14 from uv's python-build-standalone distribution, locked production wheels, migrations and
deployment assets. It does not require system Python, pip or uv to run, install or update. This is
a self-contained Python application, not a claim of Go-like static machine-code compilation.
PostgreSQL, Redis, Docker, systemd and the official gh verification client remain host services.

PyInstaller is build-only. Its documented dynamic-import collection limitations make freezing
the entire plugin/SDK-heavy backend less reliable than shipping its complete Python environment.
Nuitka would add a compiler toolchain and plugin maintenance without removing native SDK/data
requirements. PEX/shiv would still require a suitable interpreter. Existing uv owns interpreter
distribution and locked package installation; there is no custom dependency resolver or updater.

References: https://pyinstaller.org/en/stable/operating-mode.html and
https://docs.astral.sh/uv/guides/install-python/ .

## Runtime contract

`opsmesh-server version`, `check`, `api`, `worker`, `migrate` and `updater` use only the interpreter
and wheels inside the extracted archive. Paths are resolved relative to the executable launcher;
configuration remains in the caller's working directory/environment. No secrets enter archives.
Systemd uses the same launcher. The independent updater receives its own copy of the server bundle,
not an environment resolved from the network and not a symlink to the mutable current release.

Each build is extracted into a different directory before execution. Native CLI checks run with
an isolated PATH and exercise both version and an actual loopback administrative HTTP request.
Server acceptance uses an otherwise Python-free container, real PostgreSQL/Redis, migrations,
API/worker readiness and updater startup. Only those tested archives enter signing/publication.
The release manifest and checksums enumerate native archives as well as Python developer packages.
