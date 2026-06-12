FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system opsmesh \
    && adduser --system --ingroup opsmesh opsmesh

COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
COPY scripts ./scripts

RUN sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
    && pip install --upgrade pip \
    && pip install . \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && mkdir -p /app/.opsmesh-storage \
    && chown -R opsmesh:opsmesh /app

USER opsmesh

EXPOSE 8000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
