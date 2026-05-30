#!/usr/bin/env sh
set -eu

compose_file="${CHAINCLOUD_COMPOSE_FILE:-deploy/server/docker-compose.backend.yml}"
env_file="${CHAINCLOUD_ENV_FILE:-.env}"
api_container="${CHAINCLOUD_API_CONTAINER:-chaincloud-api}"
api_url="${CHAINCLOUD_API_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health/ready}"

curl -fsS "${api_url}"
docker compose --env-file "${env_file}" -f "${compose_file}" ps
docker exec "${api_container}" alembic current
