#!/usr/bin/env sh
set -eu

compose_file="${CHAINCLOUD_COMPOSE_FILE:-deploy/server/docker-compose.backend.yml}"
env_file="${CHAINCLOUD_ENV_FILE:-.env}"
api_container="${CHAINCLOUD_API_CONTAINER:-chaincloud-api}"
api_url="${CHAINCLOUD_API_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health/ready}"
prometheus_url="${CHAINCLOUD_PROMETHEUS_READY_URL:-http://127.0.0.1:9090/-/ready}"
alertmanager_url="${CHAINCLOUD_ALERTMANAGER_READY_URL:-http://127.0.0.1:9093/-/ready}"
grafana_url="${CHAINCLOUD_GRAFANA_HEALTH_URL:-http://127.0.0.1:3000/api/health}"

curl -fsS "${api_url}"
docker compose --env-file "${env_file}" -f "${compose_file}" ps
docker exec "${api_container}" alembic current

if [ "${CHAINCLOUD_SMOKE_MONITORING:-false}" = "true" ]; then
    curl -fsS "${prometheus_url}"
    curl -fsS "${alertmanager_url}"
    curl -fsS "${grafana_url}"
fi
