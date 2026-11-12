#!/usr/bin/env bash
# Install a verified Linux binary archive as a systemd service.
set -Eeuo pipefail
umask 027

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sudo bash install.sh --archive FILE --checksums FILE [options]
  --manifest FILE  Initial or explicitly updated host JSON configuration
  --env-file FILE  Initial or explicitly updated systemd environment file
  --rollback       Switch to the previous framework release

First installation defaults to an empty plugin list and no credentials.
Existing configuration is preserved unless explicitly replaced. This installer
does not install plugins or manage their databases. Python, pip, Git, Node,
Docker, and PostgreSQL are not required.
EOF
}

archive='' checksums='' manifest='' environment='' rollback=false
while (($#)); do
    case "$1" in
        --archive|--checksums|--manifest|--env-file)
            (($# >= 2)) || fail "Missing value for $1"
            case "$1" in
                --archive) archive=$(realpath "$2");;
                --checksums) checksums=$(realpath "$2");;
                --manifest) manifest=$(realpath "$2");;
                --env-file) environment=$(realpath "$2");;
            esac
            shift 2;;
        --rollback) rollback=true; shift;;
        --help|-h) usage; exit 0;;
        *) fail "Unknown argument: $1";;
    esac
done
[[ $(uname -s) == Linux ]] || fail "Only Linux is supported"
[[ $EUID == 0 ]] || fail "Run with sudo or as root"
[[ -d /run/systemd/system ]] || fail "systemd is required; Docker is an optional separate deployment"
for command in systemctl systemd-run tar sha256sum getconf sort install flock useradd getent realpath readlink awk grep mktemp; do
    command -v "$command" >/dev/null || fail "Missing system command: $command"
done
case $(uname -m) in
    x86_64) architecture=x86_64;;
    aarch64|arm64) architecture=aarch64;;
    *) fail "Unsupported CPU architecture";;
esac
glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null) || fail "glibc is required; musl/Alpine is unsupported"
glibc=${glibc#glibc }
install -d -m 0755 /opt/opsmesh /opt/opsmesh/releases
exec 9>/opt/opsmesh/install.lock
flock -n 9 || fail "Another installation is running"
previous=$(readlink -f /opt/opsmesh/current || true)

switch_binary() {
    ln -s "$1" "/opt/opsmesh/current.next.$$"
    mv -Tf "/opt/opsmesh/current.next.$$" /opt/opsmesh/current
}

if $rollback; then
    [[ -z $archive && -z $checksums && -z $manifest && -z $environment ]] || fail "--rollback cannot replace configuration or install an archive"
    target=$(readlink -f /opt/opsmesh/previous || true)
    [[ $target == /opt/opsmesh/releases/* && -x $target/opsmesh && -f $target/deploy/systemd/opsmesh.service ]] || fail "No previous release"
    systemd-run --quiet --wait --pipe --collect \
        --property=User=opsmesh --property=Group=opsmesh \
        --property=WorkingDirectory=/var/lib/opsmesh \
        --property=EnvironmentFile=-/etc/opsmesh/opsmesh.env \
        "$target/opsmesh" --manifest /etc/opsmesh/host.json --check
    systemctl stop opsmesh
    if ! { install -m 0644 "$target/deploy/systemd/opsmesh.service" /etc/systemd/system/opsmesh.service &&
        systemctl daemon-reload && switch_binary "$target" && systemctl start opsmesh; }; then
        if [[ -n $previous ]]; then
            switch_binary "$previous" || true
            install -m 0644 "$previous/deploy/systemd/opsmesh.service" /etc/systemd/system/opsmesh.service || true
            systemctl daemon-reload || true
        fi
        systemctl start opsmesh || true
        fail "Previous framework release could not start with current configuration"
    fi
    printf 'Rolled back framework to %s; plugin files, data, and configuration were preserved.\n' "$target"
    exit 0
fi

[[ -f $archive && -f $checksums ]] || fail "--archive and --checksums are required"
filename=$(basename "$archive")
[[ $filename =~ ^opsmesh-[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?-linux-${architecture}\.tar\.gz$ ]] || fail "Archive name or architecture mismatch"
expected=$(awk -v file="$filename" '$2 == file {print $1}' "$checksums")
[[ $expected =~ ^[0-9a-f]{64}$ ]] || fail "Exactly one matching SHA256 checksum is required"
actual=$(sha256sum "$archive")
[[ ${actual%% *} == "$expected" ]] || fail "Archive checksum mismatch"
[[ -z $manifest || -f $manifest ]] || fail "Manifest file does not exist"
[[ -z $environment || -f $environment ]] || fail "Environment file does not exist"
getent passwd opsmesh >/dev/null || useradd --system --user-group --home-dir /var/lib/opsmesh --shell /usr/sbin/nologin opsmesh
install -d -m 0750 -o root -g opsmesh /etc/opsmesh
install -d -m 0750 -o opsmesh -g opsmesh /var/lib/opsmesh
stage=$(mktemp -d /opt/opsmesh/releases/.staging.XXXXXX)
config_backup=$(mktemp -d /etc/opsmesh/.backup.XXXXXX)
changed=false stopped=false unit_changed=false success=false
cleanup() {
    local status=$?
    set +e
    if ! $success; then
        if $changed; then
            for file in host.json opsmesh.env; do
                if [[ -f $config_backup/$file ]]; then
                    cp -p "$config_backup/$file" "/etc/opsmesh/$file"
                else
                    rm -f "/etc/opsmesh/$file"
                fi
            done
        fi
        if $unit_changed; then
            if [[ -f $config_backup/opsmesh.service ]]; then
                cp -p "$config_backup/opsmesh.service" /etc/systemd/system/opsmesh.service
            else
                systemctl disable opsmesh || true
                rm -f /etc/systemd/system/opsmesh.service
            fi
            systemctl daemon-reload
        fi
        if $stopped && [[ -n $previous ]]; then
            systemctl stop opsmesh || true
            switch_binary "$previous"
            systemctl start opsmesh || true
        elif $stopped; then
            systemctl stop opsmesh || true
            systemctl disable opsmesh || true
            rm -f /opt/opsmesh/current
        fi
    fi
    rm -rf -- "$stage" "$config_backup"
    rm -f "/opt/opsmesh/current.next.$$"
    exit "$status"
}
trap cleanup EXIT
for file in host.json opsmesh.env; do
    [[ ! -f /etc/opsmesh/$file ]] || cp -p "/etc/opsmesh/$file" "$config_backup/$file"
done
[[ ! -f /etc/systemd/system/opsmesh.service ]] || cp -p /etc/systemd/system/opsmesh.service "$config_backup/opsmesh.service"
# Reject absolute and traversal paths before extracting a verified release.
tar -tzf "$archive" > "$stage/members"
if grep -Eq '(^/|(^|/)\.\.(/|$))' "$stage/members"; then
    fail "Invalid archive paths"
fi
tar -xzf "$archive" --strip-components=1 --no-same-owner -C "$stage"
[[ -x $stage/opsmesh && -f $stage/GLIBC_MIN && -f $stage/deploy/host.example.json && -f $stage/deploy/systemd/opsmesh.service ]] || fail "Not an OpsMesh framework release"
minimum=$(cat "$stage/GLIBC_MIN")
[[ $minimum =~ ^[0-9]+\.[0-9]+$ ]] || fail "Invalid runtime requirements"
[[ $(printf '%s\n' "$minimum" "$glibc" | sort -V | head -1) == "$minimum" ]] || fail "glibc $minimum or newer is required"
"$stage/opsmesh" --version
release="/opt/opsmesh/releases/${filename%.tar.gz}-${expected:0:12}"
rm -f "$stage/members"
chmod 0755 "$stage"
if [[ ! -e $release ]]; then
    mv "$stage" "$release"
    stage=$(mktemp -d /opt/opsmesh/releases/.staging.XXXXXX)
fi
changed=true
if [[ -z $manifest && ! -f /etc/opsmesh/host.json ]]; then
    manifest="$release/deploy/host.example.json"
fi
[[ -z $manifest ]] || install -m 0640 -o root -g opsmesh "$manifest" /etc/opsmesh/host.json
[[ -z $environment ]] || install -m 0640 -o root -g opsmesh "$environment" /etc/opsmesh/opsmesh.env

check_configuration() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=opsmesh --property=Group=opsmesh \
        --property=WorkingDirectory=/var/lib/opsmesh \
        --property=EnvironmentFile=-/etc/opsmesh/opsmesh.env \
        "$release/opsmesh" --manifest /etc/opsmesh/host.json --check
}
check_configuration
if systemctl cat opsmesh >/dev/null 2>&1; then
    systemctl stop opsmesh
fi
stopped=true
unit_changed=true
install -m 0644 "$release/deploy/systemd/opsmesh.service" /etc/systemd/system/opsmesh.service
switch_binary "$release"
systemctl daemon-reload
systemctl enable opsmesh
systemctl start opsmesh
if [[ -n $previous && $previous != "$release" ]]; then
    ln -sfn "$previous" /opt/opsmesh/previous
fi
success=true
printf 'Installed %s\nLogs: journalctl -u opsmesh -f\n' "$release"
