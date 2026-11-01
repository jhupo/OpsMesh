# opsmesh

An asynchronous, event-driven agent and bot platform. OpenAI Agents SDK is the first execution backend; other SDKs require explicit backend adapters and capability validation.

[中文](README.md) · [v0.3.2 release notes](docs/releases/v0.3.2.md) · [Design and coverage](docs/README.md) · [HTTP API guide](docs/runtime-api.md) · [Contributing](CONTRIBUTING.md) · [MIT](LICENSE)

Early developer release `0.3.2` includes a NoneBot-style plugin framework, local accounts and permissions, versioned agents and model profiles, durable runs and approvals, tools/skills/MCP/knowledge, workspace snapshots, sandbox integration, managed memory jobs, HTTP/SSE, independent DingTalk and official QQ bot adapters, schedules, audit, usage and budgets. Platform and personal model defaults, encrypted credentials, online release staging and reviewed conversation batching share the platform APIs. See the [bot adapter architecture and setup](docs/design/bot-adapters.md).

This release replaces the architecture previously published as `0.2.x` and earlier. Existing databases, configuration files, plugins and checkpoints are not compatible by assumption. Back up the previous deployment, install into a separate directory and database, and follow the [release notes](docs/releases/v0.3.0.md). There is no automatic upgrade from the old architecture.

## Install a Linux service

Download `opsmesh-0.3.2-linux.tar.gz` and its `.sha256` file from the [v0.3.2 release](https://github.com/jhupo/OpsMesh/releases/tag/v0.3.2), then verify, extract and install:

```sh
sha256sum --check opsmesh-0.3.2-linux.tar.gz.sha256
tar -xzf opsmesh-0.3.2-linux.tar.gz
cd opsmesh-0.3.2-linux
sudo bash install.sh
sudo systemctl status opsmesh.service --no-pager
sudo journalctl -u opsmesh.service -f
```

The bundle contains a wheel, hash-locked runtime requirements and deployment files. It does not ship Python, a virtual environment or the development source tree. Native installation reuses an accessible Python 3.11–3.14 when available; otherwise it downloads managed Python 3.13. It then creates an instance-specific virtual environment and installs locked dependencies online. The wheel is a standard Python package, not a standalone native executable; this is not an offline dependency bundle.

The default is a native installation managed by systemd, with SQLite and the API/background coordinator in one service. Configuration lives in `/etc/opsmesh/opsmesh.env`; persistent data lives in `/var/lib/opsmesh`. Docker and Kubernetes are not required. Select Compose explicitly with `sudo bash install.sh --mode docker`; that mode requires Docker Engine and Docker Compose and installs the wheel and dependencies into an image for API/Worker, alongside PostgreSQL and Redis.

The installer preserves configuration and data, and requires `--upgrade` when replacing an installation bundle. See the [Linux installation and operations guide](deploy/linux.md) for upgrades, multiple instances and service management. It installs no Windows service. The original `v0.3.0` release has no Linux bundle; developers can build one from current source with `uv run python scripts/build_linux_release.py --output-dir dist/linux`.

## Install a release manually

Python 3.11+ is required. Create a virtual environment with `python -m venv .venv`, then activate it using `. .venv/bin/activate` on Linux/macOS or `.venv\Scripts\Activate.ps1` in Windows PowerShell.

```sh
python -m pip install "opsmesh[openai] @ https://github.com/jhupo/OpsMesh/releases/download/v0.3.2/opsmesh-0.3.2-py3-none-any.whl"
opsmesh --version
opsmesh init
opsmesh check
opsmesh serve
```

The `openai` extra installs the OpenAI Agents SDK backend and tracing dependencies. Omit it for an SDK-independent core installation, or use `[openai,s3]` to include S3 storage. This installs the wheel directly from GitHub Releases and does not depend on a PyPI publication.

## Develop from source

Use uv 0.13.5 from a checkout of this release:

```sh
uv sync --locked --dev --extra openai
uv run --extra openai opsmesh init
uv run --extra openai opsmesh serve
```

Initialization prompts for a local administrator password. SQLite and `.opsmesh/` storage are the default. Open `http://127.0.0.1:8000/docs` for the HTTP API. Configure credentials only when executing real model requests. Environment settings use the `OPSMESH_` prefix; use `uv run --env-file .env` to explicitly load an environment file.

PostgreSQL, Redis, local/S3 blob storage, offline backup/restore, Python/Docker workers, per-run Kubernetes Jobs and OpenTelemetry export are supported through explicit configuration. Tests use the actual SDK Runner with controlled models, temporary PostgreSQL 17.11 and Redis instances, local MCP/OneBot/OTLP protocol servers, and native Linux sandbox sessions. Provider billing, external bots and production clusters require deployment acceptance. A separate Linux acceptance environment has validated Docker resource limits and cancellation, native and mutual-TLS remote sandboxes, S3/PostgreSQL backup recovery, real Responses streaming and approval restart, and K3s Job lifecycles. Native memory, isolated child sandboxes, typed run overrides, hosted MCP, Responses WebSocket, hybrid retrieval, versioned resource pricing and reconciliation are implemented. See the [coverage matrix](docs/design/sdk-coverage.md) and [deployment guide](deploy/README.md) for limitations. Web UI directories are reserved; Claude adaptation is future work.

```sh
uv run --extra openai ruff check .
uv run --extra openai ruff format --check .
uv run --extra openai mypy
uv run --extra openai pytest
uv run python scripts/check_repository.py
uv build
```

The [plugin example](examples/custom_plugin/README.md) demonstrates public service providers and code extensions. SDK-specific objects remain in the OpenAI backend. `bootstrap` owns startup composition, `application` owns cross-module execution flows, and `infrastructure/run_workers` implements isolated Run workers. The 17 business domains expose their contracts through `api.py` and keep private implementations in `internal`.

Release packages are published through [GitHub Releases](https://github.com/jhupo/OpsMesh/releases). CI validates Python 3.11–3.14 on Linux and exercises native systemd and Docker installation. Windows is outside the release support scope. Check the [actual CI runs](https://github.com/jhupo/OpsMesh/actions); local validation alone does not establish matrix coverage. The project does not provide a hosted service or imply a PyPI publication.
