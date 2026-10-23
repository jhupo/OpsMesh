# Contributing To OpsMesh

Thanks for taking the time to improve OpsMesh. This project is API-only and safety-sensitive, so changes should preserve workspace isolation, auditability, and fail-closed behavior for risky execution.

## Development Setup

```bash
uv sync --frozen --all-groups
cp deploy/local/env.example .env
uv run ruff check .
```

Run the local container stack when you need Postgres, Redis, API, and worker processes together:

```bash
docker compose -f deploy/local/compose.yml up --build
```

For development outside containers, provision PostgreSQL, Redis and persistent Temporal,
then run the same server command implementation from the checkout:

```bash
uv run opsmesh-server migrate
uv run opsmesh-server api
uv run opsmesh-server worker
uv run opsmesh-server gateway
```

Each application command runs in its own terminal. Production deployment uses either
prebuilt Docker images or the native Linux distribution; see [deployment](deploy/server/DEPLOYMENT.md).

## Pull Requests

- Create a feature branch and open a PR targeting `master`; do not push directly to `master`
  or bypass required checks. The repository workflows under `.github/workflows/` are the source
  of truth for automated checks.
- Keep changes scoped to one feature or fix.
- Retain tests for isolation, refusal, idempotency, retry, recovery and redaction. Run tests only when the user explicitly requests them.
- Do not add compatibility aliases, deprecated shims, fallback branches, or duplicate implementations.
- Do not put fake provider, mock runner, or local-only test switches in `src/opsmesh`.
- Keep credentials, API keys, private provider URLs and local build artifacts out of commits.
- HTTP API schemas come from the running server at `/openapi.json`; the SDK is for plugin development.
- Production imports use `opsmesh.*` from `src/opsmesh`; tests and migrations are separate root directories. Do not add old-path wrappers.
- Run targeted `ruff check` for affected modules; do not automatically run pytest or product acceptance scripts.
- CI selects affected existing tests on pull requests; releases request the complete retained suite. Local assistant test runs still require an explicit user request.

## Safety Expectations

- User-owned data must be queried with owner scope and explicit grants; do not restore removed workspace/marketplace review APIs.
- Agent configurations, execution instances and workflow instances have separate owners and lifetimes. Run completion or cancellation must not delete persistent resources.
- Plugin setup registers resources without opening connections or starting tasks. Plugin installation executes trusted Python declarations; archive-only inspection is available separately.
- Untrusted code execution uses an explicitly authorized sandbox. Native SDK protocols own model, MCP and sandbox effects; product authorization stays at the owning boundary.

Read [AGENTS.md](AGENTS.md), the relevant [maintainer reference](.agents/references/README.md), and [current test coverage](tests/README.md) before changing lifecycle boundaries. Generated OpenAPI comes from current routes; there is no business API client SDK.
