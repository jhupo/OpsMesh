FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPSMESH_DATA_DIRECTORY=/var/lib/opsmesh
ENV PATH="/opt/opsmesh/.venv/bin:$PATH"
WORKDIR /opt/opsmesh
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/opsmesh/requirements.lock
COPY packages/ /opt/opsmesh/packages/
COPY deploy/install/check_ready.py /opt/opsmesh/deploy/install/check_ready.py
RUN uv venv --python /usr/local/bin/python /opt/opsmesh/.venv \
    && uv pip sync --python /opt/opsmesh/.venv/bin/python --require-hashes requirements.lock \
    && uv pip install --python /opt/opsmesh/.venv/bin/python --no-deps packages/*.whl \
    && groupadd --gid 10001 opsmesh \
    && useradd --uid 10001 --gid 10001 --no-create-home opsmesh \
    && mkdir -p /var/lib/opsmesh \
    && chown 10001:10001 /var/lib/opsmesh
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["opsmesh"]
CMD ["serve", "--host", "0.0.0.0"]
