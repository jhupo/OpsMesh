# Working on opsmesh

Read `docs/README.md`, `docs/design/architecture.md` and `docs/roadmap.md` before implementing a feature. The repository contains a working development implementation. Check the coverage matrix and tests; do not treat every target design item as validated.

- Use Python src layout and the existing uv lockfile. Bootstrap is the composition root.
- Read `docs/design/source-layout.md` for file ownership. Concrete bot protocols and Run worker transports belong in `infrastructure/bots` and `infrastructure/run_workers`; bootstrap supplies their deployment configuration. Application use cases and background coordination belong in `application` and receive public service ports from bootstrap. Framework abstractions must not import platform infrastructure or application assembly. CLI implementations belong in `entrypoints/cli`.
- Modules own their data and expose `api.py`/`events.py`; do not import another module's internal code. Framework code must not depend on platform business modules.
- Platform-owned state, authorization and core use cases must work without business plugins. Core modules and entrypoints must not import `opsmesh.plugins`; optional plugins consume public module services through dependency providers. Bot-to-Agent bindings belong to bots; durable schedules belong to scheduling.
- SDK-specific types and imports belong to their backend package. Plugin target APIs follow the NoneBot-style design; do not add a competing plugin registrar without an ADR.
- Keep Session history, Run checkpoints, sandbox session state, workspace snapshots and learned memory distinct.
- Preserve local user/department/role authentication scope. External OIDC/SSO and Web UI implementation are outside the current scope.
- Add meaningful tests for actual behavior, update implementation status, and do not create successful no-op service stubs.
- Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run python scripts/check_repository.py` and `uv build` for relevant implementation work.
- Model/network integration tests must be explicit and must not run by default in CI. SDK surface checks must not call paid APIs.
- Never commit credentials, runtime state, generated workspaces or model conversation data.
