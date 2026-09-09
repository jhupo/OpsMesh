#!/usr/bin/env sh
set -eu

opsmesh_root="${OPSMESH_ROOT:-/opt/opsmesh}"
current_link="${OPSMESH_CURRENT_LINK:-${opsmesh_root}/current}"
env_file="${OPSMESH_ENV_FILE:-${opsmesh_root}/.env}"

if [ -f "${env_file}" ] && [ "${OPSMESH_SMOKE_ENV_LOADED:-false}" != "true" ]; then
    export OPSMESH_SMOKE_ENV_LOADED=true
    exec "${current_link}/python/bin/python3" -m dotenv -f "${env_file}" run -- /bin/sh "$0"
fi

api_service="${OPSMESH_API_SERVICE:-opsmesh-api}"
worker_service="${OPSMESH_WORKER_SERVICE:-opsmesh-worker}"
observability_service="${OPSMESH_OBSERVABILITY_SERVICE:-opsmesh-observability}"
systemctl_bin="${OPSMESH_SYSTEMCTL:-systemctl}"
api_base_url="${OPSMESH_API_BASE_URL:-http://127.0.0.1:8000}"
api_url="${OPSMESH_API_HEALTH_URL:-${api_base_url}/api/v1/health/ready}"
prometheus_base_url="${OPSMESH_PROMETHEUS_BASE_URL:-http://127.0.0.1:9090}"
prometheus_url="${OPSMESH_PROMETHEUS_READY_URL:-${prometheus_base_url}/-/ready}"
alertmanager_url="${OPSMESH_ALERTMANAGER_READY_URL:-http://127.0.0.1:9093/-/ready}"
grafana_url="${OPSMESH_GRAFANA_HEALTH_URL:-http://127.0.0.1:3000/api/health}"
loki_base_url="${OPSMESH_LOKI_BASE_URL:-http://127.0.0.1:3100}"
tempo_base_url="${OPSMESH_TEMPO_BASE_URL:-http://127.0.0.1:3200}"
otel_health_url="${OPSMESH_OTEL_HEALTH_URL:-http://127.0.0.1:13133/}"
docker_check_user="${OPSMESH_DOCKER_CHECK_USER:-opsmesh-worker}"

json_has_data() {
    "${current_link}/python/bin/python3" -c \
        'import json, sys; data=json.load(sys.stdin).get("data", {}).get("result", []); raise SystemExit(0 if data else 1)'
}

wait_for_http() {
    url="$1"
    attempts=30
    while [ "${attempts}" -gt 0 ]; do
        if curl -fsS "${url}" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 2
    done
    echo "Endpoint did not become ready: ${url}" >&2
    return 1
}

wait_for_prometheus_series() {
    expression="$1"
    attempts=15
    while [ "${attempts}" -gt 0 ]; do
        if curl -fsSG "${prometheus_base_url}/api/v1/query" \
            --data-urlencode "query=${expression}" | json_has_data; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 2
    done
    echo "Prometheus query returned no series: ${expression}" >&2
    return 1
}

wait_for_loki_logs() {
    attempts=15
    start_nanoseconds="$("${current_link}/python/bin/python3" -c 'import time; print(time.time_ns() - 300_000_000_000)')"
    while [ "${attempts}" -gt 0 ]; do
        if curl -fsSG "${loki_base_url}/loki/api/v1/query_range" \
            --data-urlencode 'query={service_name="opsmesh-api"}' \
            --data-urlencode "start=${start_nanoseconds}" | json_has_data; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 2
    done
    echo "Loki did not return recent OpsMesh service logs" >&2
    return 1
}

wait_for_trace() {
    trace_id="$1"
    attempts=15
    while [ "${attempts}" -gt 0 ]; do
        if curl -fsS "${tempo_base_url}/api/traces/${trace_id}" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 2
    done
    echo "Tempo did not return smoke trace ${trace_id}" >&2
    return 1
}

wait_for_http "${api_url}"
"${systemctl_bin}" is-active --quiet "${api_service}"
"${systemctl_bin}" is-active --quiet "${worker_service}"

if [ ! -x "${current_link}/opsmesh-server" ]; then
    echo "Missing server executable: ${current_link}/opsmesh-server" >&2
    exit 1
fi

(
    cd "${current_link}"
    ./opsmesh-server migrate current
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
    "${systemctl_bin}" is-active --quiet "${observability_service}"
    wait_for_http "${prometheus_url}"
    wait_for_http "${alertmanager_url}"
    wait_for_http "${grafana_url}"
    wait_for_http "${loki_base_url}/ready"
    wait_for_http "${tempo_base_url}/ready"
    wait_for_http "${otel_health_url}"

    alertmanager_config="${OPSMESH_ALERTMANAGER_CONFIG_FILE:-}"
    if [ -z "${alertmanager_config}" ] || [ ! -r "${alertmanager_config}" ]; then
        echo "Rendered Alertmanager config is missing or unreadable" >&2
        exit 1
    fi
    if ! grep -q "webhook_configs:" "${alertmanager_config}" || grep -q "\.invalid" "${alertmanager_config}"; then
        echo "Alertmanager must use a rendered non-placeholder webhook config" >&2
        exit 1
    fi

    wait_for_prometheus_series 'up{job="opsmesh-api"} == 1'
    wait_for_prometheus_series 'opsmesh_audit_integrity_workspaces >= 0'
    wait_for_prometheus_series 'opsmesh_model_usage_records_24h >= 0'

    curl -sS -o /dev/null "${api_base_url}/api/v1/health"
    wait_for_loki_logs

    if [ "${OPSMESH_SMOKE_TELEMETRY:-true}" = "true" ]; then
        trace_id="$("${current_link}/python/bin/python3" -c 'import secrets; print(secrets.token_hex(16))')"
        span_id="$("${current_link}/python/bin/python3" -c 'import secrets; print(secrets.token_hex(8))')"
        curl -sS -o /dev/null \
            -H "traceparent: 00-${trace_id}-${span_id}-01" \
            "${api_base_url}/api/v1/does-not-exist"
        wait_for_trace "${trace_id}"
    fi
fi
