#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="${CHAINCLOUD_REMOTE_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
run_id="${CHAINCLOUD_REMOTE_VALIDATION_RUN_ID:-$(date +%Y%m%d%H%M%S)-$$}"
network="chaincloud-remote-validation-${run_id}"
postgres_container="${network}-postgres"
redis_container="${network}-redis"
test_container="${network}-tests"

postgres_user="${CHAINCLOUD_REMOTE_POSTGRES_USER:-chaincloud_test}"
postgres_password="${CHAINCLOUD_REMOTE_POSTGRES_PASSWORD:-chaincloud_test}"
postgres_db="${CHAINCLOUD_REMOTE_POSTGRES_DB:-chaincloud_test}"
postgres_image="${CHAINCLOUD_REMOTE_POSTGRES_IMAGE:-postgres:16-alpine}"
redis_image="${CHAINCLOUD_REMOTE_REDIS_IMAGE:-redis:7-alpine}"
test_image="${CHAINCLOUD_REMOTE_TEST_IMAGE:-python:3.13-slim}"

pytest_args="${CHAINCLOUD_REMOTE_PYTEST_ARGS:-backend/tests/test_capabilities_api.py backend/tests/test_operations_api.py -k 'governance_reenables_disabled_mcp_tools or governance_repairs_unallowed_agent_mcp_tools or workspace_capability_governance_can_refresh_stale_mcp_health_checks or team_runtime_timeline_aggregates_redacts_and_scopes_events or operations_overview_includes_workspace_data_lifecycle_rollup'}"
ruff_args="${CHAINCLOUD_REMOTE_RUFF_ARGS:-backend/app/capabilities/service.py backend/app/operations/timeline.py backend/app/operations/service.py backend/app/api/schemas/operations.py backend/tests/test_capabilities_api.py backend/tests/test_operations_api.py}"

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
    -e "CHAINCLOUD_ENVIRONMENT=test" \
    -e "CHAINCLOUD_DATABASE_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "CHAINCLOUD_TEST_POSTGRES_URL=postgresql+psycopg://${postgres_user}:${postgres_password}@${postgres_container}:5432/${postgres_db}" \
    -e "CHAINCLOUD_REDIS_URL=redis://${redis_container}:6379/0" \
    "${test_image}" \
    sh -lc "
        python -m pip install --upgrade pip &&
        python -m pip install -e . pytest fakeredis ruff &&
        python -m pytest ${pytest_args} &&
        python -m ruff check ${ruff_args}
    "
