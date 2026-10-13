#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="${OPSMESH_REMOTE_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
run_id="${OPSMESH_REMOTE_VALIDATION_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
network="opsmesh-remote-validation-${run_id}"
postgres_container="${network}-postgres"
redis_container="${network}-redis"
test_container="${network}-tests"

postgres_user="${OPSMESH_REMOTE_POSTGRES_USER:-opsmesh_test}"
postgres_password="${OPSMESH_REMOTE_POSTGRES_PASSWORD:-opsmesh_test}"
postgres_db="${OPSMESH_REMOTE_POSTGRES_DB:-opsmesh_test}"
postgres_image="${OPSMESH_REMOTE_POSTGRES_IMAGE:-pgvector/pgvector:0.8.6-pg16-bookworm}"
redis_image="${OPSMESH_REMOTE_REDIS_IMAGE:-redis:7-alpine}"
test_image="${OPSMESH_REMOTE_TEST_IMAGE:-ghcr.io/astral-sh/uv:0.12.10-python3.12-bookworm-slim}"

if (( $# )); then
    pytest_args=("$@")
else
    pytest_args=(
        tests/test_temporal_outbox_postgres.py
        tests/test_temporal_node_execution_postgres.py
        tests/test_plugin_download_temporal_postgres.py
        tests/test_operations_api.py
    )
fi

cleanup() {
    docker rm -f "${postgres_container}" "${redis_container}" "${test_container}" >/dev/null 2>&1 || true
    docker network rm "${network}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "${network}" >/dev/null
docker run -d \
    --name "${postgres_container}" \
    --network "${network}" \
    -e "POSTGRES_USER=${postgres_user}" \
    -e "POSTGRES_PASSWORD=${postgres_password}" \
    -e "POSTGRES_DB=${postgres_db}" \
    "${postgres_image}" >/dev/null
docker run -d \
    --name "${redis_container}" \
    --network "${network}" \
    "${redis_image}" >/dev/null

for _ in $(seq 1 60); do
    if docker exec "${postgres_container}" pg_isready \
        -U "${postgres_user}" \
        -d "${postgres_db}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "${postgres_container}" pg_isready -U "${postgres_user}" -d "${postgres_db}"

for _ in $(seq 1 60); do
    if docker exec "${redis_container}" redis-cli ping >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "${redis_container}" redis-cli ping >/dev/null

docker run --rm \
    --name "${test_container}" \
    --network "${network}" \
    -v "${repo_root}:/workspace" \
    -w /workspace \
    -e "OPSMESH_ENVIRONMENT=test" \
    -e "OPSMESH_DATABASE_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "OPSMESH_TEST_POSTGRES_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "OPSMESH_REDIS_URL=redis://${redis_container}:6379/0" \
    -e OPSMESH_TEST_TEMPORAL=1 \
    -e UV_PROJECT_ENVIRONMENT=/tmp/opsmesh-validation-env \
    "${test_image}" \
    bash -c '
        uv sync --frozen --all-groups &&
        uv run --frozen pytest "$@" &&
        uv run --frozen ruff check src/opsmesh scripts tests
    ' opsmesh-validation "${pytest_args[@]}"
