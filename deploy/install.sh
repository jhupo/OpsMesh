#!/bin/sh
set -eu

repository="${OPSMESH_REPOSITORY:-jhupo/OpsMesh}"
version=""
origin=""
install_root="/opt/opsmesh"
mode="compose"

usage() {
    cat <<'EOF'
Usage: install.sh --version TAG --origin HTTPS_ORIGIN [options]

Options:
  --repository OWNER/REPOSITORY  Release repository (default: jhupo/OpsMesh)
  --root PATH                    Installation root (default: /opt/opsmesh)
  --mode compose|systemd         Application deployment mode (default: compose)
  -h, --help                     Show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            [ "$#" -ge 2 ] || { echo "install.sh: --version requires a value" >&2; exit 2; }
            version="$2"
            shift 2
            ;;
        --origin)
            [ "$#" -ge 2 ] || { echo "install.sh: --origin requires a value" >&2; exit 2; }
            origin="$2"
            shift 2
            ;;
        --repository)
            [ "$#" -ge 2 ] || { echo "install.sh: --repository requires a value" >&2; exit 2; }
            repository="$2"
            shift 2
            ;;
        --root)
            [ "$#" -ge 2 ] || { echo "install.sh: --root requires a value" >&2; exit 2; }
            install_root="$2"
            shift 2
            ;;
        --mode)
            [ "$#" -ge 2 ] || { echo "install.sh: --mode requires a value" >&2; exit 2; }
            mode="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[ "$(id -u)" -eq 0 ] || {
    echo "install.sh: run as root, for example: curl .../install.sh | sudo sh -s -- ..." >&2
    exit 1
}
[ "$(uname -s)" = "Linux" ] || { echo "install.sh: Linux is required" >&2; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { echo "install.sh: Linux amd64 is required" >&2; exit 1; }
printf '%s\n' "$repository" | grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' || {
    echo "install.sh: invalid repository" >&2
    exit 2
}
printf '%s\n' "$version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$' || {
    echo "install.sh: --version must be vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCHrcN" >&2
    exit 2
}
case "$origin" in
    https://?*) ;;
    *) echo "install.sh: --origin must be an HTTPS origin" >&2; exit 2 ;;
esac
case "$mode" in
    compose|systemd) ;;
    *) echo "install.sh: --mode must be compose or systemd" >&2; exit 2 ;;
esac
[ -d /run/systemd/system ] || {
    echo "install.sh: a systemd host is required" >&2
    exit 1
}

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
else
    echo "install.sh: cannot identify the Linux distribution" >&2
    exit 1
fi
case "${ID:-}" in
    ubuntu|debian) ;;
    *)
        echo "install.sh: automatic prerequisites currently support Ubuntu and Debian" >&2
        exit 1
        ;;
esac

packages=""
command -v curl >/dev/null 2>&1 || packages="$packages curl"
command -v tar >/dev/null 2>&1 || packages="$packages tar"
command -v sha256sum >/dev/null 2>&1 || packages="$packages coreutils"
if ! command -v pg_dump >/dev/null 2>&1 || ! command -v pg_restore >/dev/null 2>&1; then
    packages="$packages postgresql-client"
fi
command -v docker >/dev/null 2>&1 || packages="$packages docker.io"
dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null | grep -q 'install ok installed' || \
    packages="$packages ca-certificates"

if [ -n "$packages" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    # Intentional word splitting: packages is assembled only from fixed names above.
    # shellcheck disable=SC2086
    apt-get install -y $packages
fi

if ! docker compose version >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
        apt-get install -y docker-compose-v2
    elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
        apt-get install -y docker-compose-plugin
    else
        echo "install.sh: Docker Compose v2 is unavailable from the configured package sources" >&2
        exit 1
    fi
fi

systemctl enable --now docker.service
docker info >/dev/null

work_directory="$(mktemp -d /tmp/opsmesh-install.XXXXXX)"
trap 'rm -rf "$work_directory"' EXIT HUP INT TERM
archive="opsmesh-cli-${version}-linux-amd64.tar.gz"
release_api_url="https://api.github.com/repos/${repository}/releases/tags/${version}"

download_from_github_api() {
    download_url="$1"
    download_output="$2"
    download_accept="$3"
    shift 3
    curl --proto '=https' --tlsv1.2 --fail --location \
        --retry 5 --retry-all-errors --retry-delay 2 --retry-max-time 900 \
        --connect-timeout 20 --speed-limit 1024 --speed-time 30 \
        --header "Accept: $download_accept" \
        --header 'X-GitHub-Api-Version: 2022-11-28' \
        "$@" --output "$download_output" "$download_url"
}

download_from_github_api "$release_api_url" "$work_directory/release.json" \
    'application/vnd.github+json'
grep -F "\"tag_name\": \"$version\"" "$work_directory/release.json" >/dev/null || {
    echo "install.sh: release identity mismatch" >&2
    exit 1
}

release_asset_url() {
    awk -v target="$1" '
        BEGIN { RS = "\\{" }
        index($0, "\"name\": \"" target "\"") {
            if (match($0, /"url":[[:space:]]*"[^"]+"/)) {
                value = substr($0, RSTART, RLENGTH)
                sub(/^"url":[[:space:]]*"/, "", value)
                sub(/"$/, "", value)
                print value
            }
        }
    ' "$work_directory/release.json"
}

download_release_file() {
    asset_url="$(release_asset_url "$1")"
    asset_count="$(printf '%s\n' "$asset_url" | awk 'NF { count++ } END { print count + 0 }')"
    [ "$asset_count" -eq 1 ] && \
        printf '%s\n' "$asset_url" | grep -Eq \
            "^https://api\\.github\\.com/repos/${repository}/releases/assets/[0-9]+$" || {
        echo "install.sh: release asset identity mismatch for $1" >&2
        exit 1
    }
    download_from_github_api "$asset_url" "$2" 'application/octet-stream' --continue-at -
}

download_release_file "checksums.txt" "$work_directory/checksums.txt"
download_release_file "$archive" "$work_directory/$archive"

checksum_count="$(awk -v name="$archive" '$2 == name { count++ } END { print count + 0 }' \
    "$work_directory/checksums.txt")"
checksum_line="$(awk -v name="$archive" '$2 == name { print }' \
    "$work_directory/checksums.txt")"
[ -n "$checksum_line" ] && [ "$checksum_count" -eq 1 ] || {
    echo "install.sh: release checksum entry is missing or ambiguous" >&2
    exit 1
}
(cd "$work_directory" && printf '%s\n' "$checksum_line" | sha256sum -c -)

mkdir "$work_directory/cli"
tar -xzf "$work_directory/$archive" -C "$work_directory/cli"
"$work_directory/cli/opsmesh" --root "$install_root" doctor
"$work_directory/cli/opsmesh" --root "$install_root" install \
    --version "$version" --origin "$origin" --mode "$mode" --repository "$repository"

# Keep the host operator available for later status, update and credential recovery commands.
install -d -m 0750 "$install_root/operator"
cp -a "$work_directory/cli/." "$install_root/operator/"
ln -sfn "$install_root/operator/opsmesh" /usr/local/bin/opsmesh

echo "OpsMesh $version installed under $install_root"
