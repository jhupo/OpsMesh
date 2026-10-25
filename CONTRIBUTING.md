# Contributing to OpsMesh

Thank you for contributing. Discuss substantial changes in an issue before implementation; small fixes can go directly to a pull request. Use the issue templates for reproducible bugs and feature proposals. Be respectful and focus on technical evidence.

## Development

The server requires Python 3.12+ and PostgreSQL. The independent plugin SDK supports Python 3.11.8+. Production executable bundles target Linux x86_64 and aarch64, glibc 2.35+. Windows can be used for source development but is not a production distribution target.

```sh
uv sync --frozen --extra dev --extra sandbox --extra build
uv run --no-sync ruff format --check src examples tests scripts
uv run --no-sync ruff check src examples tests scripts
uv run --no-sync mypy
uv run --no-sync lint-imports --no-cache
```

Use `.env.example` for a separate development database and data directory. Never connect development tooling to production storage. Do not include credentials, logs, database dumps, generated environments or private plugin data in commits.

## Changes and verification

Follow [AGENTS.md](AGENTS.md) and the contracts in [docs](docs/). Prefer native OpenAI Agents SDK interfaces over host replacements. The framework owns its extension contracts and never imports its generated SDK. Group access permits invoking an agent; it never grants another user's workspace access.

For automated contributors, tests, test collection, service acceptance scripts and model calls require an explicit user request. Static checks and building distributions do not imply runtime verification. Existing regression code is under `tests/`; some paths need PostgreSQL, sandbox infrastructure or external credentials. Record the exact checks performed, checks skipped and integration gaps in each pull request. Do not claim unexecuted checks passed.

Include documentation for changed configuration, CLI, deployment, SDK and externally visible behavior. HTTP documentation comes from the running application's `/openapi.json`; do not maintain a handwritten schema snapshot. New database changes require a new migration; do not rewrite an already released migration.

## Releases

Update the host version and `CHANGELOG.md`. The maintainer pushes a matching stable `vMAJOR.MINOR.PATCH` tag to run `.github/workflows/release.yml`. Static checks and security jobs gate native builds, Python packages, source archives, SBOMs and multi-platform GHCR images. The final job adds provenance, SHA-256 checksums and release notes, and promotes scanned images to version, minor and latest tags. Manual dispatch builds Actions artifacts without publishing a release or image.

The `sdk` branch is the independent SDK project, with `src/opsmesh_sdk` at its root. Framework extension contracts are defined once in `src/opsmesh/contracts`. `scripts/generate_sdk.py /path/to/sdk-checkout` generates runtime bindings to those types and standalone typing stubs. Only the data-only plugin manifest is copied for offline ZIP packaging. Python 3.11.8+ can package/type-check plugins; runtime bindings execute inside the Python 3.12+ host. Release tooling pins the SDK project in `tool.opsmesh.sdk`, stages it separately and generates matching bindings before building its wheel/sdist. Executable bundles include generated bindings for hosted plugins without making them a framework dependency. Host source archives do not embed the SDK project. Do not create tags, publish packages or push branches without maintainer authorization. PyPI publication is not configured; use matching release wheels. Updating the remote SDK branch remains a separate, explicitly authorized operation.

Source distributions use the explicit allowlist in `scripts/release_assets.py`. Keep private data, local environments, credentials and acceptance artifacts outside published assets.

## License

Contributions are provided under MIT, as detailed in [LICENSE](LICENSE). Do not remove third-party notices from redistributed dependencies. Report security issues privately using [SECURITY.md](SECURITY.md).
