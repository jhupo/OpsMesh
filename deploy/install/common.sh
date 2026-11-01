#!/usr/bin/env bash
# Shared installer policy and release verification; sourcing this file makes no changes.

fail() { printf 'opsmesh install: %s\n' "$*" >&2; exit 1; }
inform() { printf 'opsmesh install: %s\n' "$*"; }

usage() {
    cat <<'USAGE'
Usage: sudo bash install.sh [options]

  --mode local|docker           Deployment mode (default: local systemd service)
  --instance NAME               Isolated installation name (default: opsmesh)
  --port PORT                   Initial HTTP port (default: 8000)
  --admin USER                  First administrator username (default: admin)
  --admin-password-file PATH     Read initial password from a private file
  --admin-password-stdin         Read initial password from stdin without a file
  --upgrade                     Stop app services and explicitly migrate schema
  --expect-installed-version V   Refuse if target changed after online update check
  --no-start                    Prepare and initialize, without starting app services
  --help                        Show this help

Run from an extracted OpsMesh Linux release bundle, not a source checkout.
Linux only. Local mode requires systemd, curl, Git and standard GNU utilities.
An accessible local Python 3.11-3.14 is reused; otherwise Python is downloaded.
Docker mode requires an installed Docker Engine and Docker Compose v2.
Configuration and data survive repeated installs and upgrades. Existing configuration
is never overwritten; --port applies only when creating a new configuration file.
USAGE
}

parse_arguments() {
    MODE=local INSTANCE=opsmesh PORT=8000 ADMIN_USERNAME=admin PASSWORD_FILE=
    UPGRADE=false START_APPLICATION=true PASSWORD_STDIN=false EXPECT_INSTALLED_VERSION=
    while (( $# )); do
        case $1 in
            --mode|--instance|--port|--admin|--admin-password-file|--expect-installed-version)
                (( $# >= 2 )) && [[ $2 != --* ]] || fail "Missing value for $1"
                case $1 in
                    --mode) MODE=$2 ;;
                    --instance) INSTANCE=$2 ;;
                    --port) PORT=$2 ;;
                    --admin) ADMIN_USERNAME=$2 ;;
                    --admin-password-file) PASSWORD_FILE=$2 ;;
                    --expect-installed-version) EXPECT_INSTALLED_VERSION=$2 ;;
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
    BUNDLE_DIRECTORY=$(cd -- "$INSTALL_SCRIPT_DIRECTORY/.." && pwd -P)
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
    validate_release_bundle
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
        require_commands systemctl systemd-run systemd-analyze useradd groupadd getent id curl git runuser
        [[ -d /run/systemd/system ]] || fail 'Local installation requires systemd running as PID 1'
    else
        require_commands docker od tr
        docker info >/dev/null || fail 'Docker daemon is unavailable'
        docker compose version >/dev/null || fail 'Docker Compose v2 is required'
    fi
    validate_new_instance_identity
}

validate_release_bundle() {
    [[ -f $BUNDLE_DIRECTORY/VERSION && -f $BUNDLE_DIRECTORY/SHA256SUMS && \
        -f $BUNDLE_DIRECTORY/requirements.lock ]] ||
        fail 'A Linux release bundle is required. Build it with scripts/build_linux_release.py, then extract it and run install.sh'
    [[ -z $(find "$BUNDLE_DIRECTORY" -type l -print -quit) ]] ||
        fail 'The release bundle must not contain symbolic links'
    BUNDLE_VERSION=$(<"$BUNDLE_DIRECTORY/VERSION")
    [[ $BUNDLE_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$ ]] ||
        fail 'Invalid release VERSION'
    WHEEL_FILENAME=opsmesh-$BUNDLE_VERSION-py3-none-any.whl
    local -a wheels=("$BUNDLE_DIRECTORY"/packages/*.whl)
    [[ ${#wheels[@]} == 1 && ${wheels[0]} == "$BUNDLE_DIRECTORY/packages/$WHEEL_FILENAME" && \
        -f ${wheels[0]} ]] || fail 'The release bundle must contain exactly its versioned OpsMesh wheel'
    BUNDLE_FILES=(
        install.sh VERSION requirements.lock README.md LICENSE "packages/$WHEEL_FILENAME"
        deploy/install.sh deploy/install/common.sh deploy/install/local.sh deploy/install/docker.sh
        deploy/install/check_ready.py deploy/systemd/opsmesh.service deploy/compose.yaml deploy/Dockerfile
    )
    local -A expected_files=() verified_files=()
    local file line hash manifest_path
    for file in "${BUNDLE_FILES[@]}"; do
        [[ -f $BUNDLE_DIRECTORY/$file ]] || fail "Release file is missing: $file"
        expected_files[$file]=1
    done
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line =~ ^([a-f0-9]{64})[[:space:]][[:space:]]([^[:space:]]+)$ ]] ||
            fail 'Invalid SHA256SUMS entry'
        hash=${BASH_REMATCH[1]}
        manifest_path=${BASH_REMATCH[2]}
        [[ -n ${expected_files[$manifest_path]:-} && -z ${verified_files[$manifest_path]:-} ]] ||
            fail "Unexpected or repeated release checksum path: $manifest_path"
        verified_files[$manifest_path]=$hash
    done < "$BUNDLE_DIRECTORY/SHA256SUMS"
    [[ ${#verified_files[@]} == ${#expected_files[@]} ]] || fail 'SHA256SUMS does not cover all release files'
    (cd "$BUNDLE_DIRECTORY" && sha256sum --check --status --strict SHA256SUMS) ||
        fail 'Release checksum validation failed'
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

release_payload() {
    tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
        -C "$BUNDLE_DIRECTORY" -cf - "${BUNDLE_FILES[@]}" SHA256SUMS
}

inspect_installation_state() {
    # The lock owns this decision: another first installer may have run since preflight.
    if [[ -n $EXPECT_INSTALLED_VERSION ]]; then
        [[ $UPGRADE == true && -f $CURRENT_RELEASE/VERSION ]] || fail 'Online update target is unavailable'
        [[ $(<"$CURRENT_RELEASE/VERSION") == "$EXPECT_INSTALLED_VERSION" ]] ||
            fail 'Installed version changed after update check; check the target again'
    fi
    if [[ -f $MODE_FILE ]]; then
        [[ $(<"$MODE_FILE") == "$MODE" ]] ||
            fail 'Installed mode differs; use another --instance for the other mode'
    else
        validate_new_instance_identity
        printf '%s\n' "$MODE" > "$MODE_FILE"
        chmod 0600 "$MODE_FILE"
    fi
    ARTIFACT_DIGEST=$(release_payload | sha256sum | cut -d ' ' -f 1)
    REUSE_RELEASE=false
    if [[ -f $DIGEST_FILE && $UPGRADE == false ]]; then
        [[ $(<"$DIGEST_FILE") == "$ARTIFACT_DIGEST" ]] ||
            fail 'Installed release artifacts differ; back up data and rerun with --upgrade'
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
    release_payload | tar -C "$RELEASE_DIRECTORY" -xf -
    chmod -R go-w "$RELEASE_DIRECTORY"
}

activate_release() {
    if [[ $REUSE_RELEASE == false ]]; then
        ln -s -- "$RELEASE_DIRECTORY" "$INSTALL_DIRECTORY/current.next.$$"
        mv -Tf -- "$INSTALL_DIRECTORY/current.next.$$" "$CURRENT_RELEASE"
    fi
    printf '%s\n' "$ARTIFACT_DIGEST" > "$DIGEST_FILE"
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
