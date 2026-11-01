#!/usr/bin/env bash
# Shared installer policy and source preparation; sourcing this file makes no changes.

fail() { printf 'opsmesh install: %s\n' "$*" >&2; exit 1; }
inform() { printf 'opsmesh install: %s\n' "$*"; }

usage() {
    cat <<'USAGE'
Usage: sudo bash deploy/install.sh [options]

  --mode local|docker           Deployment mode (default: local systemd service)
  --instance NAME               Isolated installation name (default: opsmesh)
  --port PORT                   Initial HTTP port (default: 8000)
  --admin USER                  First administrator username (default: admin)
  --admin-password-file PATH     Read initial password from a private file
  --admin-password-stdin         Read initial password from stdin without a file
  --upgrade                     Stop app services and explicitly migrate schema
  --no-start                    Prepare and initialize, without starting app services
  --help                        Show this help

Linux only. Local mode requires systemd, curl, Git and standard GNU utilities.
Docker mode requires an installed Docker Engine and Docker Compose v2.
Configuration and data survive repeated installs and upgrades. Existing configuration
is never overwritten; --port applies only when creating a new configuration file.
USAGE
}

parse_arguments() {
    MODE=local INSTANCE=opsmesh PORT=8000 ADMIN_USERNAME=admin PASSWORD_FILE=
    UPGRADE=false START_APPLICATION=true PASSWORD_STDIN=false
    while (( $# )); do
        case $1 in
            --mode|--instance|--port|--admin|--admin-password-file)
                (( $# >= 2 )) && [[ $2 != --* ]] || fail "Missing value for $1"
                case $1 in
                    --mode) MODE=$2 ;;
                    --instance) INSTANCE=$2 ;;
                    --port) PORT=$2 ;;
                    --admin) ADMIN_USERNAME=$2 ;;
                    --admin-password-file) PASSWORD_FILE=$2 ;;
                esac
                shift 2 ;;
            --upgrade) UPGRADE=true; shift ;;
            --admin-password-stdin) PASSWORD_STDIN=true; shift ;;
            --no-start) START_APPLICATION=false; shift ;;
            --help|-h) usage; exit 0 ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
    [[ $MODE == local || $MODE == docker ]] || fail 'Mode must be local or docker'
    [[ $PASSWORD_STDIN == false || -z $PASSWORD_FILE ]] ||
        fail '--admin-password-stdin and --admin-password-file are mutually exclusive'
    [[ $INSTANCE =~ ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ || $INSTANCE =~ ^[a-z]$ ]] ||
        fail 'Instance must be 1-32 lowercase letters/digits/hyphens, beginning with a letter'
    [[ $PORT =~ ^[0-9]{1,5}$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) ||
        fail 'Port must be between 1 and 65535'
    PORT=$((10#$PORT))
    [[ $ADMIN_USERNAME =~ ^[a-z][a-z0-9._-]{2,63}$ ]] ||
        fail 'Administrator username must match [a-z][a-z0-9._-]{2,63}'
}

configure_installation_paths() {
    SOURCE_DIRECTORY=$(cd -- "$INSTALL_SCRIPT_DIRECTORY/.." && pwd -P)
    INSTALL_DIRECTORY=/opt/$INSTANCE
    CONFIG_DIRECTORY=/etc/$INSTANCE
    CONFIG_FILE=$CONFIG_DIRECTORY/opsmesh.env
    DATA_DIRECTORY=/var/lib/$INSTANCE
    SERVICE_FILE=/etc/systemd/system/$INSTANCE.service
    CURRENT_RELEASE=$INSTALL_DIRECTORY/current
    INITIALIZED_FILE=$CONFIG_DIRECTORY/initialized
    DIGEST_FILE=$CONFIG_DIRECTORY/source.sha256
    MODE_FILE=$CONFIG_DIRECTORY/install-mode
    UV_VERSION=0.13.5
    RELEASE_DIRECTORY=
}

require_commands() {
    local command_name
    for command_name in "$@"; do
        command -v "$command_name" >/dev/null 2>&1 || fail "Required command is missing: $command_name"
    done
}

validate_installation() {
    [[ $(uname -s) == Linux ]] || fail 'This installer supports Linux only'
    (( EUID == 0 )) || fail 'Run this installer with sudo or as root'
    require_commands tar sha256sum install stat readlink flock find cut date grep chmod chown \
        ln mv mktemp sed
    [[ -f $SOURCE_DIRECTORY/pyproject.toml && -f $SOURCE_DIRECTORY/uv.lock ]] ||
        fail 'Run the installer from a complete OpsMesh checkout or source archive'
    [[ -z $(find "$SOURCE_DIRECTORY/src" "$SOURCE_DIRECTORY/deploy" -type l -print -quit) ]] ||
        fail 'Installation source must not contain symbolic links'
    local target
    for target in "$INSTALL_DIRECTORY" "$CONFIG_DIRECTORY" "$DATA_DIRECTORY" \
        "$CONFIG_FILE" "$MODE_FILE" "$INITIALIZED_FILE" "$DIGEST_FILE"; do
        [[ ! -L $target ]] || fail "Refusing symbolic link: $target"
        if [[ -e $target && $target != "$DATA_DIRECTORY" ]]; then
            [[ $(stat -c %u "$target") == 0 ]] || fail "Expected root ownership: $target"
        fi
    done
    if [[ ! -f $MODE_FILE ]]; then
        for target in "$INSTALL_DIRECTORY" "$DATA_DIRECTORY"; do
            [[ ! -e $target ]] || fail "Unmanaged path already exists: $target; choose another --instance"
        done
        [[ ! -e $SERVICE_FILE && ! -L $SERVICE_FILE ]] || fail "Unmanaged service exists: $SERVICE_FILE"
        [[ $UPGRADE == false ]] || fail '--upgrade requires a previously installed instance'
    else
        [[ $(<"$MODE_FILE") == "$MODE" ]] || fail 'Installed mode differs; use another --instance for the other mode'
    fi
    if [[ -e $CONFIG_FILE ]]; then
        [[ -f $CONFIG_FILE ]] || fail 'Configuration must be a regular file'
        if grep -Eq '^[[:space:]]*(export[[:space:]]+)?OPSMESH_ADMIN_PASSWORD[[:space:]]*=' "$CONFIG_FILE"; then
            fail 'Remove OPSMESH_ADMIN_PASSWORD from persistent configuration; use --admin-password-stdin or --admin-password-file'
        fi
    fi
    if [[ -n $PASSWORD_FILE ]]; then
        [[ -f $PASSWORD_FILE && ! -L $PASSWORD_FILE ]] || fail 'Password file must be a regular non-symlink file'
        local password_mode
        password_mode=$(stat -c %a "$PASSWORD_FILE")
        (( (8#$password_mode & 077) == 0 )) || fail 'Password file must not be readable by group or others (use chmod 600)'
    fi
    if [[ $MODE == local ]]; then
        require_commands systemctl systemd-run systemd-analyze useradd groupadd getent id curl git
        [[ -d /run/systemd/system ]] || fail 'Local installation requires systemd running as PID 1'
    else
        require_commands docker od tr
        docker info >/dev/null || fail 'Docker daemon is unavailable'
        docker compose version >/dev/null || fail 'Docker Compose v2 is required'
    fi
    validate_new_instance_identity
}

validate_new_instance_identity() {
    [[ ! -f $MODE_FILE ]] || return 0
    if [[ $MODE == local ]]; then
        if getent passwd "$INSTANCE" >/dev/null || getent group "$INSTANCE" >/dev/null; then
            fail "An unmanaged account or group named $INSTANCE already exists; choose another --instance"
        fi
    else
        local resources resource_type
        for resource_type in container volume network; do
            local -a list_options=(ls --quiet --filter "label=com.docker.compose.project=$INSTANCE")
            if [[ $resource_type == container ]]; then list_options+=(--all); fi
            resources=$(docker "$resource_type" "${list_options[@]}") ||
                fail "Could not inspect Docker $resource_type ownership"
            [[ -z $resources ]] ||
                fail "Unmanaged Compose project $INSTANCE has existing $resource_type resources; choose another --instance"
        done
    fi
}

prepare_installation_directories() {
    install -d -m 0755 "$INSTALL_DIRECTORY" "$INSTALL_DIRECTORY/releases"
    install -d -m 0750 "$CONFIG_DIRECTORY"
}

acquire_installation_lock() {
    exec {INSTALL_LOCK_DESCRIPTOR}>"$CONFIG_DIRECTORY/install.lock"
    flock -n "$INSTALL_LOCK_DESCRIPTOR" || fail 'Another installer is operating on this instance'
}

source_payload() {
    tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
        --exclude=__pycache__ --exclude='*.pyc' --exclude='*.pyo' \
        --exclude=.env --exclude='.env.*' --exclude='*.env' \
        -C "$SOURCE_DIRECTORY" -cf - src pyproject.toml uv.lock README.md LICENSE deploy .dockerignore
}

inspect_installation_state() {
    # The lock owns this decision: another first installer may have run since preflight.
    if [[ -f $MODE_FILE ]]; then
        [[ $(<"$MODE_FILE") == "$MODE" ]] ||
            fail 'Installed mode differs; use another --instance for the other mode'
    else
        validate_new_instance_identity
        printf '%s\n' "$MODE" > "$MODE_FILE"
        chmod 0600 "$MODE_FILE"
    fi
    SOURCE_DIGEST=$(source_payload | sha256sum | cut -d ' ' -f 1)
    REUSE_RELEASE=false
    if [[ -f $DIGEST_FILE && $UPGRADE == false ]]; then
        [[ $(<"$DIGEST_FILE") == "$SOURCE_DIGEST" ]] ||
            fail 'Installed source differs; back up data and rerun with --upgrade'
        [[ -L $CURRENT_RELEASE && -d $CURRENT_RELEASE ]] || fail 'Installed current release link is missing'
        RELEASE_DIRECTORY=$(readlink -f -- "$CURRENT_RELEASE")
        [[ $RELEASE_DIRECTORY == "$INSTALL_DIRECTORY/releases/"* ]] || fail 'Current release points outside this installation'
        REUSE_RELEASE=true
    fi
}

prepare_release() {
    [[ $REUSE_RELEASE == false ]] || return 0
    RELEASE_DIRECTORY=$INSTALL_DIRECTORY/releases/$(date -u +%Y%m%dT%H%M%SZ)-$$
    install -d -m 0755 "$RELEASE_DIRECTORY"
    source_payload | tar -C "$RELEASE_DIRECTORY" -xf -
    chmod -R go-w "$RELEASE_DIRECTORY"
}

activate_release() {
    if [[ $REUSE_RELEASE == false ]]; then
        ln -s -- "$RELEASE_DIRECTORY" "$INSTALL_DIRECTORY/current.next.$$"
        mv -Tf -- "$INSTALL_DIRECTORY/current.next.$$" "$CURRENT_RELEASE"
    fi
    printf '%s\n' "$SOURCE_DIGEST" > "$DIGEST_FILE"
    chmod 0600 "$DIGEST_FILE"
}

read_administrator_password() {
    [[ ! -f $INITIALIZED_FILE ]] || return 0
    local password confirmation
    if [[ ${PASSWORD_STDIN:-false} == true ]]; then
        IFS= read -r -s password || [[ -n $password ]] ||
            fail 'No administrator password received on stdin'
    elif [[ -n $PASSWORD_FILE ]]; then
        password=$(<"$PASSWORD_FILE")
    else
        [[ -t 0 ]] || fail 'First installation needs a terminal, --admin-password-stdin or --admin-password-file'
        IFS= read -r -s -p 'First administrator password: ' password
        printf '\n'
        IFS= read -r -s -p 'Confirm administrator password: ' confirmation
        printf '\n'
        [[ $password == "$confirmation" ]] || fail 'Passwords do not match'
    fi
    (( ${#password} >= 12 && ${#password} <= 1024 )) || fail 'Password must contain 12-1024 characters'
    [[ $password != *$'\n'* && $password != *$'\r'* ]] || fail 'Password must be a single line'
    # Keep the password out of the build/download process environment. Only the
    # one-shot initialization command receives it, by environment variable name.
    ADMINISTRATOR_PASSWORD=$password
    export -n ADMINISTRATOR_PASSWORD
}

mark_initialized() {
    printf 'initialized\n' > "$INITIALIZED_FILE"
    chmod 0600 "$INITIALIZED_FILE"
    unset ADMINISTRATOR_PASSWORD OPSMESH_ADMIN_PASSWORD
}
