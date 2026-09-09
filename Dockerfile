FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY operator ./operator
COPY runtime ./runtime
COPY backend ./backend
RUN uv sync --frozen --no-dev --no-editable --package opsmesh

FROM python:3.12-slim
ARG BUILD_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" \
    OPSMESH_BUILD_COMMIT=${BUILD_COMMIT} OPSMESH_RUN_MIGRATIONS=false
WORKDIR /app
RUN addgroup --system opsmesh && adduser --system --ingroup opsmesh opsmesh \
    && mkdir -p /app/.opsmesh-storage && chown opsmesh:opsmesh /app/.opsmesh-storage
COPY --from=builder /app/.venv /app/.venv
COPY backend/migrations ./backend/migrations
COPY alembic.ini ./
USER opsmesh
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
