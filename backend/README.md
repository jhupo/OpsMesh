# ChainCloud Backend

Backend service for ChainCloud Agent Team.

## Local Development

Install dependencies:

```bash
uv sync --dev
```

Run the API:

```bash
uv run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/api/v1/health
```

Quality checks:

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

Run database migrations:

```bash
uv run alembic upgrade head
```
