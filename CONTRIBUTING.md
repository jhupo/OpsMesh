# Contributing to OpsMesh

Thank you for contributing. Discuss substantial changes in an issue before implementation; small fixes can go directly to a pull request. Use the issue templates for reproducible bugs and feature proposals. Be respectful and focus on technical evidence.

## Development

The server requires Python 3.12+ and PostgreSQL. The independent plugin SDK supports Python 3.11.8+. Production executable bundles target Linux x86_64 and aarch64, glibc 2.35+. Windows can be used for source development but is not a production distribution target.

```sh
uv sync --frozen --extra dev --extra sandbox --extra build
uv run --no-sync ruff format --check src sdk/python/src sdk/python/examples examples tests scripts
uv run --no-sync ruff check src sdk/python/src sdk/python/examples examples tests scripts
uv run --no-sync mypy
uv run --no-sync lint-imports --no-cache
uv run --no-sync mypy --python-version 3.11 sdk/python/src
```

Use `.env.example` for a separate development database and data directory. Never connect development tooling to production storage. Do not include credentials, logs, database dumps, generated environments or private plugin data in commits.

## Changes and verification

Follow [AGENTS.md](AGENTS.md) and the contracts in [docs](docs/). Prefer native OpenAI Agents SDK interfaces over host replacements. Keep the SDK independent of the host. Group access permits invoking an agent; it never grants another user's workspace access.

For automated contributors, tests, test collection, service acceptance scripts and model calls require an explicit user request. Static checks and building distributions do not imply runtime verification. Existing regression code is under `tests/`; some paths need PostgreSQL, sandbox infrastructure or external credentials. Record the exact checks performed, checks skipped and integration gaps in each pull request. Do not claim unexecuted checks passed.

Include documentation for changed configuration, CLI, deployment, SDK and externally visible behavior. HTTP documentation comes from the running application's `/openapi.json`; do not maintain a handwritten schema snapshot. New database changes require a new migration; do not rewrite an already released migration.

## Releases

Update both project versions and `CHANGELOG.md`. The maintainer pushes a matching stable `vMAJOR.MINOR.PATCH` tag to run `.github/workflows/release.yml`. Static checks and security jobs gate native builds, Python packages, source archives, SBOMs and multi-platform GHCR images. The final job adds provenance, SHA-256 checksums and release notes, and promotes scanned images to version, minor and latest tags. Manual dispatch builds Actions artifacts without publishing a release or image.

The `sdk` branch exposes `sdk/python/` as an independent project root. SDK changes are maintained in the main tree and the branch is updated explicitly; merely editing the main tree does not update that branch. Release packages are built from the tagged main tree, with matching SDK/server versions. Do not create tags, publish packages or push branches without maintainer authorization. PyPI publication is not configured; use release wheels or the SDK Git branch.

Source distributions use the explicit allowlist in `scripts/release_assets.py`. Keep private data, local environments, credentials and acceptance artifacts outside published assets.

## License

Contributions are provided under MIT, as detailed in [LICENSE](LICENSE). Do not remove third-party notices from redistributed dependencies. Report security issues privately using [SECURITY.md](SECURITY.md).
