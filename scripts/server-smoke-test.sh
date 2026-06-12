#!/usr/bin/env sh
set -eu

opsmesh_root="${OPSMESH_ROOT:-/opt/opsmesh}"
current_link="${OPSMESH_CURRENT_LINK:-${opsmesh_root}/current}"
env_file="${OPSMESH_ENV_FILE:-${opsmesh_root}/.env}"
api_service="${OPSMESH_API_SERVICE:-opsmesh-api}"
worker_service="${OPSMESH_WORKER_SERVICE:-opsmesh-worker}"
systemctl_bin="${OPSMESH_SYSTEMCTL:-systemctl}"
api_url="${OPSMESH_API_HEALTH_URL:-http://127.0.0.1:8000/api/v1/health/ready}"
prometheus_url="${OPSMESH_PROMETHEUS_READY_URL:-http://127.0.0.1:9090/-/ready}"
alertmanager_url="${OPSMESH_ALERTMANAGER_READY_URL:-http://127.0.0.1:9093/-/ready}"
grafana_url="${OPSMESH_GRAFANA_HEALTH_URL:-http://127.0.0.1:3000/api/health}"
docker_check_user="${OPSMESH_DOCKER_CHECK_USER:-opsmesh-worker}"

if [ -f "${env_file}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${env_file}"
    set +a
fi

curl -fsS "${api_url}"
"${systemctl_bin}" is-active --quiet "${api_service}"
"${systemctl_bin}" is-active --quiet "${worker_service}"

if [ ! -x "${current_link}/.venv/bin/alembic" ]; then
    echo "Missing alembic executable: ${current_link}/.venv/bin/alembic" >&2
    exit 1
fi

(
    cd "${current_link}"
    ./.venv/bin/alembic current
)

if [ "${OPSMESH_SMOKE_DOCKER_RUNTIME:-false}" = "true" ]; then
    "${systemctl_bin}" is-active --quiet docker.service
    if command -v sudo >/dev/null 2>&1; then
        sudo -n -u "${docker_check_user}" docker info >/dev/null
    else
        docker info >/dev/null
    fi
fi

if [ "${OPSMESH_SMOKE_MONITORING:-false}" = "true" ]; then
    curl -fsS "${prometheus_url}"
    curl -fsS "${alertmanager_url}"
    curl -fsS "${grafana_url}"
fi
