#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

repo_root="${OPSMESH_CONNECTOR_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
python_bin="${OPSMESH_CONNECTOR_PYTHON:-python3}"
api_url="${OPSMESH_API_URL:-}"
credential="${OPSMESH_RUNTIME_CREDENTIAL:-}"
expected_status="${OPSMESH_CONNECTOR_EXPECTED_STATUS:-}"
poll_interval_seconds="${OPSMESH_CONNECTOR_POLL_INTERVAL_SECONDS:-2}"
timeout_seconds="${OPSMESH_CONNECTOR_SMOKE_TIMEOUT_SECONDS:-30}"

if [ -z "${api_url}" ] || [ -z "${credential}" ]; then
    echo "OPSMESH_API_URL and OPSMESH_RUNTIME_CREDENTIAL are required" >&2
    exit 2
fi

case "${expected_status}" in
    ""|idle|completed|failed|recovered) ;;
    *)
        echo "OPSMESH_CONNECTOR_EXPECTED_STATUS must be idle, completed, failed, or recovered" >&2
        exit 2
        ;;
esac

smoke_dir="${OPSMESH_CONNECTOR_SMOKE_DIR:-}"
remove_smoke_dir=false
if [ -z "${smoke_dir}" ]; then
    smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/opsmesh-connector-smoke.XXXXXX")"
    remove_smoke_dir=true
else
    mkdir -p "${smoke_dir}"
fi
chmod 700 "${smoke_dir}"

cleanup() {
    if [ "${remove_smoke_dir}" = true ]; then
        rm -rf "${smoke_dir}"
    fi
}
trap cleanup EXIT

venv_path="${OPSMESH_CONNECTOR_VENV:-${smoke_dir}/venv}"
if [ ! -x "${venv_path}/bin/python" ]; then
    "${python_bin}" -m venv "${venv_path}"
    "${venv_path}/bin/python" -m pip install --disable-pip-version-check --quiet "${repo_root}/runtime"
fi

connector_bin="${venv_path}/bin/opsmesh-self-hosted-worker"
if [ ! -x "${connector_bin}" ]; then
    echo "Connector executable was not installed: ${connector_bin}" >&2
    exit 1
fi

"${connector_bin}" --check >/dev/null
state_path="${OPSMESH_CONNECTOR_STATE_PATH:-${smoke_dir}/state.sqlite3}"
deadline="$(($(date +%s) + timeout_seconds))"

while true; do
    result="$(
        OPSMESH_API_URL="${api_url}" \
        OPSMESH_RUNTIME_CREDENTIAL="${credential}" \
        "${connector_bin}" --state-path "${state_path}" --once
    )"
    status="$(
        "${venv_path}/bin/python" -c \
            'import json, sys; value=json.load(sys.stdin); status=value.get("status"); print(status if isinstance(status, str) else "")' \
            <<<"${result}"
    )"
    case "${status}" in
        idle|completed|failed|recovered) ;;
        *)
            echo "Connector returned an invalid smoke status" >&2
            exit 1
            ;;
    esac

    printf '%s\n' "${result}"
    if [ -z "${expected_status}" ] || [ "${status}" = "${expected_status}" ]; then
        exit 0
    fi
    if [ "${status}" != idle ] || [ "$(date +%s)" -ge "${deadline}" ]; then
        echo "Expected connector status '${expected_status}', received '${status}'" >&2
        exit 1
    fi
    sleep "${poll_interval_seconds}"
done
