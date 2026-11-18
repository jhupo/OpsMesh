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
prefix=/usr/local/lib/opsmesh
releases=$prefix/releases
cli=/usr/local/bin/opsmesh
entrypoint=$prefix/current/opsmesh
install -d -m 0755 "$prefix" "$releases" /usr/local/bin
exec 9>"$prefix/install.lock"
flock -n 9 || fail "Another installation is running"
previous=''
if [[ -L $prefix/current ]]; then
    previous=$(readlink -e "$prefix/current") || fail "Broken current release link"
    [[ $previous == "$releases/"* && -x $previous/opsmesh ]] || fail "Invalid current release"
elif [[ -e $prefix/current ]]; then
    fail "Refusing to overwrite $prefix/current"
fi
if [[ -e $cli || -L $cli ]]; then
    [[ -L $cli && $(readlink "$cli") == "$entrypoint" ]] || fail "Refusing to overwrite $cli"
fi

switch_binary() {
    ln -s "$1" "$prefix/current.next.$$"
    mv -Tf "$prefix/current.next.$$" "$prefix/current"
}

has_standard_unit() {
    local unit="$1/deploy/systemd/opsmesh.service"
    [[ -f $unit ]] && grep -Fxq "ExecStart=$cli --manifest /etc/opsmesh/host.json" "$unit"
}

if $rollback; then
    [[ -z $archive && -z $checksums && -z $manifest && -z $environment ]] || fail "--rollback cannot replace configuration or install an archive"
    target=$(readlink -e "$prefix/previous" || true)
    [[ -n $previous && $target == "$releases/"* && -x $target/opsmesh ]] || fail "No previous release"
    has_standard_unit "$target" || fail "Previous release does not use the standard layout"
    [[ -L $cli ]] || fail "Missing installed command: $cli"
    installed_unit=/etc/systemd/system/opsmesh.service
    unit_backup=$(mktemp /etc/opsmesh/.rollback-unit.XXXXXX)
    trap 'rm -f -- "$unit_backup"' EXIT
    cp -p "$installed_unit" "$unit_backup"
    systemctl stop opsmesh
    if ! systemd-run --quiet --wait --pipe --collect \
        --property=User=opsmesh --property=Group=opsmesh \
        --property=WorkingDirectory=/var/lib/opsmesh \
        --property=EnvironmentFile=-/etc/opsmesh/opsmesh.env \
        "$target/opsmesh" --manifest /etc/opsmesh/host.json --check; then
        systemctl start opsmesh || true
        fail "Rollback configuration check failed"
    fi
    if ! { install -m 0644 "$target/deploy/systemd/opsmesh.service" "$installed_unit" &&
        switch_binary "$target" && systemctl daemon-reload && systemctl start opsmesh; }; then
        systemctl stop opsmesh || true
        switch_binary "$previous" || true
        cp -p "$unit_backup" "$installed_unit" || true
        systemctl daemon-reload || true
        systemctl reset-failed opsmesh || true
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
stage=$(mktemp -d "$releases/.staging.XXXXXX")
config_backup=$(mktemp -d /etc/opsmesh/.backup.XXXXXX)
changed=false stopped=false was_active=false unit_changed=false cli_created=false success=false
if systemctl is-active --quiet opsmesh; then was_active=true; fi
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
        if $stopped; then
            systemctl stop opsmesh || true
            if [[ -n $previous ]]; then
                switch_binary "$previous"
            else
                rm -f "$prefix/current"
            fi
            if $was_active; then
                systemctl reset-failed opsmesh || true
                systemctl start opsmesh || true
            fi
        fi
        if $cli_created; then rm -f "$cli"; fi
    fi
    rm -rf -- "$stage" "$config_backup"
    rm -f "$prefix/current.next.$$"
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
[[ -x $stage/opsmesh && -f $stage/GLIBC_MIN && -f $stage/deploy/host.example.json ]] || fail "Not an OpsMesh framework release"
has_standard_unit "$stage" || fail "Use the installer shipped with this archive; its systemd unit must use $cli"
minimum=$(cat "$stage/GLIBC_MIN")
[[ $minimum =~ ^[0-9]+\.[0-9]+$ ]] || fail "Invalid runtime requirements"
[[ $(printf '%s\n' "$minimum" "$glibc" | sort -V | head -1) == "$minimum" ]] || fail "glibc $minimum or newer is required"
"$stage/opsmesh" --version
release="$releases/${filename%.tar.gz}-${expected:0:12}"
rm -f "$stage/members"
chmod 0755 "$stage"
if [[ ! -e $release ]]; then
    mv "$stage" "$release"
    stage=$(mktemp -d "$releases/.staging.XXXXXX")
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
if systemctl cat opsmesh >/dev/null 2>&1; then
    systemctl stop opsmesh
fi
stopped=true
check_configuration
unit_changed=true
install -m 0644 "$release/deploy/systemd/opsmesh.service" /etc/systemd/system/opsmesh.service
switch_binary "$release"
if [[ ! -L $cli ]]; then
    ln -s "$entrypoint" "$cli"
    cli_created=true
fi
systemctl daemon-reload
systemctl enable opsmesh
systemctl start opsmesh
if [[ -n $previous && $previous != "$release" ]] && has_standard_unit "$previous"; then
    ln -sfn "$previous" "$prefix/previous"
fi
success=true
printf 'Installed %s\nCommand: %s\nLogs: journalctl -u opsmesh -f\n' "$release" "$cli"
