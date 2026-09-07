#!/usr/bin/env sh
set -eu

action="${1:-update}"
if [ "$#" -gt 0 ]; then
    shift
fi

tag=""
manifest_url=""
manifest_file=""
bundle_url=""
bundle_file=""
bundle_sha256=""
dry_run="false"
opsmesh_root="${OPSMESH_ROOT:-/opt/opsmesh}"
env_file="${OPSMESH_ENV_FILE:-${opsmesh_root}/.env}"

if [ -f "${env_file}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${env_file}"
    set +a
fi

timeout_seconds="${OPSMESH_RELEASE_UPDATE_TIMEOUT_SECONDS:-900}"
opsmesh_root="${OPSMESH_ROOT:-${opsmesh_root}}"
releases_dir="${OPSMESH_RELEASES_DIR:-${opsmesh_root}/releases}"
current_link="${OPSMESH_CURRENT_LINK:-${opsmesh_root}/current}"
state_file="${OPSMESH_RELEASE_STATE_FILE:-${opsmesh_root}/release-state.env}"
repository="${OPSMESH_RELEASE_UPDATE_REPOSITORY:-jhupo/OpsMesh}"
github_base_url="${OPSMESH_RELEASE_UPDATE_GITHUB_URL:-https://github.com}"
download_dir="${OPSMESH_RELEASE_DOWNLOAD_DIR:-${opsmesh_root}/downloads}"
api_service="${OPSMESH_API_SERVICE:-opsmesh-api}"
worker_service="${OPSMESH_WORKER_SERVICE:-opsmesh-worker}"
observability_service="${OPSMESH_OBSERVABILITY_SERVICE:-opsmesh-observability}"
observability_enabled="${OPSMESH_OBSERVABILITY_ENABLED:-false}"
systemctl_bin="${OPSMESH_SYSTEMCTL:-systemctl}"
uv_sync_args="${OPSMESH_UV_SYNC_ARGS:---frozen --no-dev}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)
            tag="${2:-}"
            shift 2
            ;;
        --manifest-url)
            manifest_url="${2:-}"
            shift 2
            ;;
        --manifest-file)
            manifest_file="${2:-}"
            shift 2
            ;;
        --bundle-url)
            bundle_url="${2:-}"
            shift 2
            ;;
        --bundle-file)
            bundle_file="${2:-}"
            shift 2
            ;;
        --bundle-sha256)
            bundle_sha256="${2:-}"
            shift 2
            ;;
        --timeout-seconds)
            timeout_seconds="${2:-}"
            shift 2
            ;;
        --dry-run)
            dry_run="true"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

case "${action}" in
    update | rollback | restart) ;;
    *)
        echo "Action must be update, rollback, or restart" >&2
        exit 2
        ;;
esac

download() {
    source_url="$1"
    target_file="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "${source_url}" -o "${target_file}"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${target_file}" "${source_url}"
    else
        echo "curl or wget is required to download release assets" >&2
        exit 1
    fi
}

json_value() {
    key="$1"
    file="$2"
    python3 - "$key" "$file" <<'PY'
import json
import sys

with open(sys.argv[2], encoding="utf-8") as handle:
    value = json.load(handle)
for part in sys.argv[1].split("."):
    if not isinstance(value, dict) or part not in value:
        print("")
        raise SystemExit
    value = value[part]
if value is None:
    print("")
else:
    print(value)
PY
}

sha256_file() {
    file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${file}" | awk '{print $1}'
    else
        shasum -a 256 "${file}" | awk '{print $1}'
    fi
}

normalize_tag() {
    candidate="$1"
    case "${candidate}" in
        v[0-9]*.[0-9]*.[0-9]* | [0-9]*.[0-9]*.[0-9]*)
            case "${candidate}" in
                v*) printf '%s\n' "${candidate}" ;;
                *) printf 'v%s\n' "${candidate}" ;;
            esac
            ;;
        *)
            echo "Release tag must look like v1.2.3" >&2
            exit 2
            ;;
    esac
}

manifest_asset_url() {
    normalized_tag="$1"
    base="${github_base_url%/}"
    printf '%s/%s/releases/download/%s/opsmesh-server-%s-manifest.json\n' \
        "${base}" "${repository}" "${normalized_tag}" "${normalized_tag}"
}

bundle_asset_url() {
    normalized_tag="$1"
    base="${github_base_url%/}"
    printf '%s/%s/releases/download/%s/opsmesh-server-%s.tar.gz\n' \
        "${base}" "${repository}" "${normalized_tag}" "${normalized_tag}"
}

load_env() {
    if [ -f "${env_file}" ]; then
        set -a
        # shellcheck disable=SC1090
        . "${env_file}"
        set +a
    fi
}

load_current_state() {
    if [ ! -f "${state_file}" ]; then
        echo "No release state file found: ${state_file}" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    . "${state_file}"
}

current_target() {
    if [ -L "${current_link}" ]; then
        readlink "${current_link}"
    elif [ -d "${current_link}" ]; then
        printf '%s\n' "${current_link}"
    else
        printf '\n'
    fi
}

smoke_script_for_current() {
    printf '%s/scripts/server-smoke-test.sh\n' "${current_link}"
}

run_with_timeout() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "${timeout_seconds}" "$@"
    else
        "$@"
    fi
}

run_smoke() {
    smoke_script="${OPSMESH_SMOKE_SCRIPT:-$(smoke_script_for_current)}"
    run_with_timeout "${smoke_script}"
}

install_release() {
    load_env
    (
        cd "${current_link}"
        uv sync ${uv_sync_args}
    )
}

run_migrations() {
    load_env
    (
        cd "${current_link}"
        ./.venv/bin/alembic upgrade head
    )
}

restart_services() {
    "${systemctl_bin}" daemon-reload
    "${systemctl_bin}" restart "${api_service}" "${worker_service}"
    if [ "${observability_enabled}" = "true" ]; then
        "${systemctl_bin}" restart "${observability_service}"
    fi
}

write_state() {
    previous_release_dir="$(current_target)"
    if [ -f "${state_file}" ]; then
        # shellcheck disable=SC1090
        . "${state_file}"
        previous_release_dir="${OPSMESH_CURRENT_RELEASE_DIR:-${previous_release_dir}}"
        previous_tag="${OPSMESH_CURRENT_RELEASE_TAG:-}"
    else
        previous_tag=""
    fi
    mkdir -p "$(dirname "${state_file}")"
    {
        echo "OPSMESH_PREVIOUS_RELEASE_DIR=${previous_release_dir}"
        echo "OPSMESH_PREVIOUS_RELEASE_TAG=${previous_tag}"
        echo "OPSMESH_CURRENT_RELEASE_DIR=${release_dir}"
        echo "OPSMESH_CURRENT_RELEASE_TAG=${tag}"
        echo "OPSMESH_CURRENT_BUNDLE_SHA256=${bundle_sha256}"
    } > "${state_file}"
}

switch_current() {
    target_dir="$1"
    parent_dir="$(dirname "${current_link}")"
    temp_link="${parent_dir}/.current.tmp.$$"
    mkdir -p "${parent_dir}"
    ln -sfn "${target_dir}" "${temp_link}"
    mv -Tf "${temp_link}" "${current_link}"
}

resolve_manifest() {
    mkdir -p "${download_dir}"
    if [ -n "${manifest_file}" ]; then
        if [ ! -f "${manifest_file}" ]; then
            echo "Manifest file not found: ${manifest_file}" >&2
            exit 1
        fi
        resolved_manifest="${manifest_file}"
        return
    fi
    if [ -z "${manifest_url}" ]; then
        manifest_url="$(manifest_asset_url "${tag}")"
    fi
    resolved_manifest="${download_dir}/opsmesh-server-${tag}-manifest.json"
    download "${manifest_url}" "${resolved_manifest}"
}

resolve_bundle() {
    manifest_bundle_url=""
    manifest_bundle_sha256=""
    if [ -n "${resolved_manifest:-}" ]; then
        manifest_bundle_url="$(json_value "bundle.url" "${resolved_manifest}")"
        manifest_bundle_sha256="$(json_value "bundle.sha256" "${resolved_manifest}")"
    fi
    if [ -z "${bundle_url}" ]; then
        bundle_url="${manifest_bundle_url}"
    fi
    if [ -z "${bundle_sha256}" ]; then
        bundle_sha256="${manifest_bundle_sha256}"
    fi
    if [ -z "${bundle_url}" ] && [ -n "${tag}" ]; then
        bundle_url="$(bundle_asset_url "${tag}")"
    fi
    if [ -z "${bundle_sha256}" ]; then
        echo "Bundle sha256 is required in manifest or --bundle-sha256" >&2
        exit 1
    fi
    if [ -n "${bundle_file}" ]; then
        if [ ! -f "${bundle_file}" ]; then
            echo "Bundle file not found: ${bundle_file}" >&2
            exit 1
        fi
        resolved_bundle="${bundle_file}"
        return
    fi
    if [ -z "${bundle_url}" ]; then
        echo "Bundle URL is required in manifest or --bundle-url" >&2
        exit 1
    fi
    if [ "${dry_run}" = "true" ]; then
        resolved_bundle="${bundle_url}"
        return
    fi
    bundle_name="bundle"
    if [ -n "${tag}" ]; then
        bundle_name="${tag}"
    fi
    resolved_bundle="${download_dir}/opsmesh-server-${bundle_name}.tar.gz"
    download "${bundle_url}" "${resolved_bundle}"
}

validate_manifest() {
    manifest_tag="$(json_value "tag" "${resolved_manifest}")"
    if [ -z "${tag}" ]; then
        tag="$(normalize_tag "${manifest_tag}")"
    fi
    if [ "$(normalize_tag "${manifest_tag}")" != "${tag}" ]; then
        echo "Manifest tag does not match requested tag" >&2
        exit 1
    fi
}

validate_bundle_manifest() {
    bundle_manifest="$1"
    manifest_tag="$(json_value "tag" "${bundle_manifest}")"
    if [ -z "${tag}" ]; then
        tag="$(normalize_tag "${manifest_tag}")"
    fi
    if [ "$(normalize_tag "${manifest_tag}")" != "${tag}" ]; then
        echo "Bundle manifest tag does not match requested tag" >&2
        exit 1
    fi
}

verify_bundle() {
    actual_sha256="$(sha256_file "${resolved_bundle}")"
    if [ "${actual_sha256}" != "${bundle_sha256}" ]; then
        echo "Bundle sha256 mismatch: expected ${bundle_sha256}, got ${actual_sha256}" >&2
        exit 1
    fi
}

unpack_bundle() {
    release_dir="${releases_dir}/${tag}"
    staging_dir="${releases_dir}/.${tag}.tmp.$$"
    rm -rf "${staging_dir}"
    mkdir -p "${staging_dir}"
    tar -xzf "${resolved_bundle}" -C "${staging_dir}"
    if [ -d "${staging_dir}/release-bundle" ]; then
        bundle_root="${staging_dir}/release-bundle"
    else
        bundle_root="${staging_dir}"
    fi
    if [ ! -f "${bundle_root}/manifest.json" ]; then
        echo "Bundle does not contain manifest.json" >&2
        exit 1
    fi
    validate_bundle_manifest "${bundle_root}/manifest.json"
    release_dir="${releases_dir}/${tag}"
    rm -rf "${release_dir}"
    mkdir -p "$(dirname "${release_dir}")"
    mv "${bundle_root}" "${release_dir}"
    rm -rf "${staging_dir}"
    chmod +x "${release_dir}/scripts/server-update.sh" "${release_dir}/scripts/server-smoke-test.sh"
}

case "${action}" in
    update)
        if [ -n "${tag}" ]; then
            tag="$(normalize_tag "${tag}")"
        elif [ -z "${manifest_file}" ] && [ -z "${manifest_url}" ] && [ -z "${bundle_file}" ] && [ -z "${bundle_url}" ]; then
            echo "Update requires --tag, --manifest-url, --manifest-file, --bundle-url, or --bundle-file" >&2
            exit 2
        fi
        if [ -n "${manifest_file}" ] && [ -n "${manifest_url}" ]; then
            echo "Use only one of --manifest-url or --manifest-file" >&2
            exit 2
        fi
        if [ -n "${bundle_file}" ] && [ -n "${bundle_url}" ]; then
            echo "Use only one of --bundle-url or --bundle-file" >&2
            exit 2
        fi
        if [ -n "${manifest_file}" ] || [ -n "${manifest_url}" ] || { [ -n "${tag}" ] && [ -z "${bundle_file}" ] && [ -z "${bundle_url}" ]; }; then
            resolve_manifest
            validate_manifest
        fi
        resolve_bundle
        release_dir="${releases_dir}/${tag:-from-bundle}"
        ;;
    rollback)
        load_current_state
        release_dir="${OPSMESH_PREVIOUS_RELEASE_DIR:-}"
        tag="${OPSMESH_PREVIOUS_RELEASE_TAG:-rollback}"
        if [ -z "${release_dir}" ]; then
            echo "No previous release recorded in ${state_file}" >&2
            exit 1
        fi
        ;;
    restart) ;;
esac

echo "OpsMesh release action"
echo "action=${action}"
echo "tag=${tag}"
echo "current_link=${current_link}"
echo "env_file=${env_file}"
echo "api_service=${api_service}"
echo "worker_service=${worker_service}"
echo "observability_service=${observability_service}"
echo "observability_enabled=${observability_enabled}"
case "${action}" in
    update)
        echo "manifest=${resolved_manifest:-}"
        echo "bundle=${resolved_bundle}"
        echo "bundle_sha256=${bundle_sha256}"
        echo "release_dir=${release_dir}"
        ;;
    rollback)
        echo "release_dir=${release_dir}"
        ;;
esac

if [ "${dry_run}" = "true" ]; then
    echo "dry_run=true"
    exit 0
fi

case "${action}" in
    update)
        verify_bundle
        unpack_bundle
        write_state
        switch_current "${release_dir}"
        install_release
        run_migrations
        restart_services
        run_smoke
        ;;
    rollback)
        switch_current "${release_dir}"
        install_release
        restart_services
        run_smoke
        ;;
    restart)
        restart_services
        run_smoke
        ;;
esac
