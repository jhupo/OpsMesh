# Contributing To OpsMesh

Thanks for taking the time to improve OpsMesh. This project is API-only and safety-sensitive, so changes should preserve workspace isolation, auditability, and fail-closed behavior for risky execution.

## Development Setup

```bash
uv sync --frozen --all-groups
cp deploy/local/env.example .env
uv run pytest tests/test_health.py
uv run ruff check .
```

Run the local container stack when you need Postgres, Redis, API, and worker processes together:

```bash
docker compose -f deploy/local/compose.yml up --build
```

## Pull Requests

- Create a feature branch and open a PR targeting `master`; do not push directly to `master`
  or bypass required checks. The repository workflows under `.github/workflows/` are the source
  of truth for automated checks.
- Keep changes scoped to one feature or fix.
- Validate affected product flows; retain tests for tenant isolation, refusal, idempotency, retry, recovery and redaction.
- Do not add compatibility aliases, deprecated shims, fallback branches, or duplicate implementations.
- Do not put fake provider, mock runner, or local-only test switches in `src/opsmesh`.
- Keep credentials, API keys, private provider URLs and local build artifacts out of commits.
- Regenerate `docs/openapi.json` and `docs/api/` after API changes with `uv run python scripts/export_api_docs.py`; verify with `--check`.
- Production imports use `opsmesh.*` from `src/opsmesh`; tests and migrations are separate root directories. Do not add old-path wrappers.
- Run targeted `ruff check` and targeted pytest tests for the affected modules before opening a PR.
- The complete pytest suite runs only in the tag-triggered release gate, not locally or on every PR.

## Safety Expectations

- Workspace-owned data must always be queried with workspace scope.
- Public marketplace resources must pass review before becoming visible.
- Private workspace resources should skip review by default unless the workspace opts in.
- LLM review failures for public or required-review paths must fail closed into admin review.
- User-controlled code must run inside Docker, a self-hosted isolated runtime, or another approved sandbox.
