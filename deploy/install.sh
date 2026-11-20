#!/usr/bin/env bash
# Install from trusted, pre-built wheels, never into the system interpreter.
set -euo pipefail
usage() { printf 'Usage: bash deploy/install.sh WHEELHOUSE [PYTHON [RUNTIME_REQUIREMENTS]]\nThe wheelhouse must contain one opsmesh_cli wheel and all runtime dependencies.\n'; }
if [[ ${1:-} == --help ]]; then usage; exit 0; fi
if (( $# < 1 || $# > 3 )); then usage >&2; exit 2; fi
[[ $(uname -s) == Linux && $EUID == 0 ]] || { echo 'Requires root on Linux with systemd' >&2; exit 1; }
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
wheels=$(cd -- "$1" && pwd -P)
python=${2:-python3}
runtime=()
if [[ -n ${3:-} ]]; then
    [[ -f $3 ]] || { echo 'Runtime requirements file not found' >&2; exit 1; }
    runtime=(-r "$3")
fi
"$python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
for cmd in systemctl systemd-sysusers flock; do command -v "$cmd" >/dev/null; done
shopt -s nullglob
service=("$wheels"/opsmesh_cli-*.whl)
(( ${#service[@]} == 1 )) || { echo 'Expected one service wheel' >&2; exit 1; }
prefix=/opt/opsmesh
install -d -o root -g root -m 0755 "$prefix" "$prefix/environments"
exec 9>"$prefix/.install.lock"
flock -n 9 || { echo 'Another installation is running' >&2; exit 1; }
if systemctl is-active --quiet opsmesh.service; then
    echo 'Stop opsmesh.service and take an offline backup before upgrading' >&2; exit 1
fi
bin=/usr/local/bin/opsmesh
if [[ -e $bin || -L $bin ]]; then
    [[ $(readlink "$bin") == "$prefix/current/bin/opsmesh" ]] || { echo "Refusing to replace unrelated $bin" >&2; exit 1; }
fi
if [[ -e /etc/systemd/system/opsmesh.service && ! -L $prefix/current ]]; then
    echo 'Existing unmanaged opsmesh unit: migrate explicitly first' >&2; exit 1
fi
envdir=$(mktemp -d "$prefix/environments/env.XXXXXXXX")
chmod 0755 "$envdir"
"$python" -m venv "$envdir"
"$envdir/bin/python" -m pip install --disable-pip-version-check --no-index --only-binary=:all: --find-links "$wheels" "${service[0]}" "${runtime[@]}"
"$envdir/bin/python" -m pip check
"$envdir/bin/opsmesh" --version
install -d /usr/lib/sysusers.d /etc/opsmesh /etc/logrotate.d /usr/local/bin
install -m 0644 "$here/opsmesh.sysusers" /usr/lib/sysusers.d/opsmesh.conf
systemd-sysusers /usr/lib/sysusers.d/opsmesh.conf
install -d -o root -g opsmesh -m 0750 /var/lib/opsmesh-inbox
install -d -o opsmesh -g opsmesh -m 0700 /var/backups/opsmesh
if [[ ! -e /etc/opsmesh/opsmesh.toml ]]; then install -m 0644 "$here/opsmesh.toml" /etc/opsmesh/opsmesh.toml; fi
install -m 0644 "$here/opsmesh.logrotate" /etc/logrotate.d/opsmesh
install -m 0644 "$here/opsmesh.service" /etc/systemd/system/opsmesh.service
previous=$(readlink "$prefix/current" || true)
ln -s "$envdir" "$envdir.link"
mv -Tf "$envdir.link" "$prefix/current"
if [[ ! -L $bin ]]; then ln -s "$prefix/current/bin/opsmesh" "$bin"; fi
systemctl daemon-reload
if ! systemctl enable --now opsmesh.service; then
    systemctl stop opsmesh.service || true
    if [[ -n $previous ]]; then ln -s "$previous" "$envdir.link"; mv -Tf "$envdir.link" "$prefix/current"; fi
    echo 'Startup failed. Inspect journalctl -u opsmesh.service; review state/config before restarting.' >&2; exit 1
fi
"$bin" service status
