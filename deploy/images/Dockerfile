FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY LICENSE COPYING LICENSE-MIT NOTICE ./
COPY operator ./operator
COPY sdk/python ./sdk/python
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --package opsmesh
RUN uv build --package opsmesh-sdk --out-dir /app/extension-sdk

FROM python:3.14-slim AS extension-builder
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
WORKDIR /opt/opsmesh-builder
COPY pyproject.toml uv.lock README.md ./
COPY operator ./operator
COPY sdk/python ./sdk/python
RUN uv sync --frozen --no-dev --no-editable --package opsmesh-sdk \
    && uv pip install --python /opt/opsmesh-builder/.venv/bin/python pip==26.2.1 setuptools==84.0.0 \
    && chmod -R a+rX /opt/opsmesh-builder/.venv
ENV PATH="/opt/opsmesh-builder/.venv/bin:$PATH" HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
WORKDIR /tmp

FROM python:3.14-slim AS base
ARG BUILD_COMMIT
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" \
    OPSMESH_BUILD_COMMIT=${BUILD_COMMIT} OPSMESH_RUN_MIGRATIONS=false
WORKDIR /app
RUN addgroup --system --gid 10001 opsmesh && adduser --system --uid 10001 --ingroup opsmesh opsmesh \
    && mkdir -p /app/.opsmesh-storage && chown opsmesh:opsmesh /app/.opsmesh-storage
COPY --from=builder /app/.venv /app/.venv
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/docker-entrypoint.sh /usr/local/bin/opsmesh-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/opsmesh-entrypoint \
    && chmod 755 /usr/local/bin/opsmesh-entrypoint
USER opsmesh
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/opsmesh-entrypoint"]

FROM base AS api
CMD ["uvicorn", "opsmesh.bootstrap.api:create_app", "--factory", "--no-proxy-headers", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
USER root
COPY --from=builder /app/extension-sdk /opt/opsmesh-extension-sdk
ENV OPSMESH_EXTENSION_SDK_WHEEL=/opt/opsmesh-extension-sdk/opsmesh_sdk-0.1.0rc23-py3-none-any.whl
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/*
USER opsmesh
CMD ["python", "-m", "opsmesh.bootstrap.worker_cli"]
