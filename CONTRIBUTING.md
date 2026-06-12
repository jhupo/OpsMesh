# Contributing To OpsMesh

Thanks for taking the time to improve OpsMesh. This project is backend-first and safety-sensitive, so changes should preserve workspace isolation, auditability, and fail-closed behavior for risky execution.

## Development Setup

```bash
uv sync --all-groups
cp .env.example .env
uv run pytest
uv run ruff check .
```

Run the local container stack when you need Postgres, Redis, API, and worker processes together:

```bash
docker compose up --build
```

## Pull Requests

- Keep changes scoped to one feature or fix.
- Add or update tests for API behavior, worker behavior, migrations, security boundaries, and review/approval flows.
- Do not add compatibility aliases for old APIs or configuration names unless there is an explicit migration task.
- Do not put fake provider, mock runner, or local-only test switches in `backend/app`.
- Keep credentials, API keys, provider base URLs, and generated artifacts out of commits.
- Run `uv run ruff check .` and `uv run pytest` before opening a PR.

## Safety Expectations

- Workspace-owned data must always be queried with workspace scope.
- Public marketplace resources must pass review before becoming visible.
- Private workspace resources should skip review by default unless the workspace opts in.
- LLM review failures for public or required-review paths must fail closed into admin review.
- User-controlled code must run inside Docker, a self-hosted isolated runtime, or another approved sandbox.

