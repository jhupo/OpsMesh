#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="${OPSMESH_REMOTE_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
run_id="${OPSMESH_REMOTE_VALIDATION_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
network="opsmesh-remote-validation-${run_id}"
postgres_container="${network}-postgres"
redis_container="${network}-redis"
temporal_container="${network}-temporal"
test_container="${network}-tests"

postgres_user="${OPSMESH_REMOTE_POSTGRES_USER:-opsmesh_test}"
postgres_password="${OPSMESH_REMOTE_POSTGRES_PASSWORD:-opsmesh_test}"
postgres_db="${OPSMESH_REMOTE_POSTGRES_DB:-opsmesh_test}"
postgres_image="${OPSMESH_REMOTE_POSTGRES_IMAGE:-postgres:16-bookworm}"
redis_image="${OPSMESH_REMOTE_REDIS_IMAGE:-redis:7-alpine}"
temporal_image="${OPSMESH_REMOTE_TEMPORAL_IMAGE:-temporalio/admin-tools:1.31.0}"
test_image="${OPSMESH_REMOTE_TEST_IMAGE:-ghcr.io/astral-sh/uv:0.12.10-python3.12-bookworm-slim}"

if (( $# )); then
    pytest_args=("$@")
else
    pytest_args=(tests operator/tests sdk/python/tests)
fi

runtime_arguments=()
if [[ -n "${OPSMESH_TEST_SANDBOX_IMAGE:-}" ]]; then
    test -S /var/run/docker.sock
    runtime_arguments+=(
        -v /var/run/docker.sock:/var/run/docker.sock
        -e "OPSMESH_TEST_SANDBOX_IMAGE=${OPSMESH_TEST_SANDBOX_IMAGE}"
    )
fi

cleanup() {
    docker rm -f "${postgres_container}" "${redis_container}" "${temporal_container}" "${test_container}" >/dev/null 2>&1 || true
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
docker run -d \
    --name "${temporal_container}" \
    --network "${network}" \
    --entrypoint temporal \
    "${temporal_image}" server start-dev --ip 0.0.0.0 \
    --db-filename /tmp/opsmesh-validation.sqlite >/dev/null

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
for _ in $(seq 1 60); do
    if docker exec "${temporal_container}" temporal operator cluster health \
        --address 127.0.0.1:7233 >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "${temporal_container}" temporal operator cluster health --address 127.0.0.1:7233

docker run --rm \
    --name "${test_container}" \
    --network "${network}" \
    -v "${repo_root}:/workspace" \
    -w /workspace \
    -e "OPSMESH_ENVIRONMENT=test" \
    -e "OPSMESH_DATABASE_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "OPSMESH_TEST_DATABASE_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "OPSMESH_REDIS_URL=redis://${redis_container}:6379/0" \
    -e "OPSMESH_TEST_REDIS_URL=redis://${redis_container}:6379/15" \
    -e "OPSMESH_TEST_TEMPORAL_ADDRESS=${temporal_container}:7233" \
    -e OPSMESH_TEST_TEMPORAL_LOCAL=1 \
    -e UV_PROJECT_ENVIRONMENT=/tmp/opsmesh-validation-env \
    "${runtime_arguments[@]}" \
    "${test_image}" \
    bash -c '
        uv sync --frozen --all-groups &&
        uv run --frozen ruff check src/opsmesh scripts tests &&
        uv run --frozen lint-imports --no-cache &&
        uv run --frozen python scripts/audit_app_layout.py --architecture-check &&
        uv run --frozen mypy &&
        uv run --frozen python scripts/export_api_docs.py --check &&
        uv run --frozen python scripts/generate_sdk_api.py --check &&
        uv run --frozen alembic upgrade head &&
        uv run --frozen pytest "$@" &&
        uv run --frozen alembic heads
    ' opsmesh-validation "${pytest_args[@]}"
