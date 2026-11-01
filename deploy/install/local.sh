#!/usr/bin/env bash
# Native Python installation and systemd lifecycle.

prepare_local_account() {
    if ! getent group "$INSTANCE" >/dev/null; then groupadd --system "$INSTANCE"; fi
    if ! getent passwd "$INSTANCE" >/dev/null; then
        useradd --system --gid "$INSTANCE" --home-dir "$DATA_DIRECTORY" \
            --no-create-home --shell /usr/sbin/nologin "$INSTANCE"
    fi
    [[ $(id -u "$INSTANCE") != 0 ]] || fail 'Service account must not be root'
    install -d -o "$INSTANCE" -g "$INSTANCE" -m 0750 "$DATA_DIRECTORY"
    chown root:"$INSTANCE" "$CONFIG_DIRECTORY"
    if [[ ! -f $CONFIG_FILE ]]; then
        (umask 077; cat > "$CONFIG_FILE" <<CONFIG
# Managed installation defaults. Existing configuration is preserved on upgrade.
OPSMESH_DATA_DIRECTORY=$DATA_DIRECTORY
OPSMESH_HOST=127.0.0.1
OPSMESH_PORT=$PORT
OPSMESH_WORKER_ENABLED=true
OPSMESH_RUN_WORKER_MODE=inline
OPSMESH_TRACING_ENABLED=false
# Optional services; SQLite is used when DATABASE_URL is absent.
# OPSMESH_DATABASE_URL=postgresql+asyncpg://user:password@127.0.0.1/opsmesh
# OPSMESH_REDIS_URL=redis://127.0.0.1:6379/0
# OPENAI_API_KEY=
CONFIG
        )
    fi
    chown root:"$INSTANCE" "$CONFIG_FILE"
    chmod 0640 "$CONFIG_FILE"
}

install_uv() {
    UV_EXECUTABLE=$INSTALL_DIRECTORY/tools/uv
    install -d -m 0755 "$INSTALL_DIRECTORY/tools" "$INSTALL_DIRECTORY/python" "$INSTALL_DIRECTORY/cache"
    if [[ -x $UV_EXECUTABLE ]] && [[ $($UV_EXECUTABLE --version | cut -d ' ' -f 2) == "$UV_VERSION" ]]; then return 0; fi
    if command -v uv >/dev/null && [[ $(uv --version | cut -d ' ' -f 2) == "$UV_VERSION" ]]; then
        install -m 0755 "$(command -v uv)" "$UV_EXECUTABLE"
        return 0
    fi
    local architecture artifact download_directory
    architecture=$(uname -m)
    [[ $architecture == x86_64 || $architecture == aarch64 ]] ||
        fail 'Automatic uv installation supports x86_64/aarch64; install uv 0.13.5 manually for this architecture'
    artifact=uv-$architecture-unknown-linux-gnu.tar.gz
    download_directory=$(mktemp -d "$INSTALL_DIRECTORY/uv-download.XXXXXX")
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --max-time 180 --retry 2 --retry-delay 2 --retry-max-time 360 \
        "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$artifact" \
        --output "$download_directory/$artifact"
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --max-time 180 --retry 2 --retry-delay 2 --retry-max-time 360 \
        "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$artifact.sha256" \
        --output "$download_directory/$artifact.sha256"
    (cd "$download_directory" && sha256sum --check "$artifact.sha256")
    tar -xzf "$download_directory/$artifact" -C "$download_directory"
    install -m 0755 "$download_directory/uv-$architecture-unknown-linux-gnu/uv" "$UV_EXECUTABLE"
    # Keep downloaded checksums with the tool installation for inspection.
    install -m 0644 "$download_directory/$artifact.sha256" "$INSTALL_DIRECTORY/tools/uv.sha256"
}

prepare_local_environment() {
    [[ $REUSE_RELEASE == false ]] || return 0
    install_uv
    env -u VIRTUAL_ENV -u PYTHONPATH \
        UV_PYTHON_INSTALL_DIR="$INSTALL_DIRECTORY/python" \
        UV_PYTHON_BIN_DIR="$INSTALL_DIRECTORY/tools" UV_CACHE_DIR="$INSTALL_DIRECTORY/cache" \
        UV_PROJECT_ENVIRONMENT="$RELEASE_DIRECTORY/.venv" \
        "$UV_EXECUTABLE" sync --directory "$RELEASE_DIRECTORY" --frozen --no-dev \
        --extra openai --extra s3 --no-editable --managed-python --python 3.13
    chmod -R go-w "$RELEASE_DIRECTORY" "$INSTALL_DIRECTORY/python"
}

run_local_command() {
    local -a command_options=(
        --quiet --wait --pipe --collect --property=Type=exec
        "--unit=$INSTANCE-setup-$$-$RANDOM"
        "--property=User=$INSTANCE" "--property=Group=$INSTANCE"
        "--property=WorkingDirectory=$DATA_DIRECTORY"
        "--property=EnvironmentFile=$CONFIG_FILE" --property=UMask=0077
        "--setenv=HOME=$DATA_DIRECTORY" --setenv=PYTHONUNBUFFERED=1
    )
    if [[ ${1:-} == init ]]; then
        # Only the variable name appears in argv; the transient unit inherits its value.
        command_options+=(--setenv=OPSMESH_ADMIN_PASSWORD)
    fi
    if [[ ${1:-} == init ]]; then
        OPSMESH_ADMIN_PASSWORD=$ADMINISTRATOR_PASSWORD \
            systemd-run "${command_options[@]}" "$RELEASE_DIRECTORY/.venv/bin/opsmesh" "$@"
    else
        systemd-run "${command_options[@]}" "$RELEASE_DIRECTORY/.venv/bin/opsmesh" "$@"
    fi
}

install_local_service() {
    local temporary_unit_directory
    temporary_unit_directory=$(mktemp -d "$INSTALL_DIRECTORY/systemd.XXXXXX")
    sed -e "s|@INSTANCE@|$INSTANCE|g" \
        -e "s|@INSTALL_DIRECTORY@|$INSTALL_DIRECTORY|g" \
        -e "s|@CONFIG_FILE@|$CONFIG_FILE|g" \
        -e "s|@DATA_DIRECTORY@|$DATA_DIRECTORY|g" \
        "$RELEASE_DIRECTORY/deploy/systemd/opsmesh.service" > "$temporary_unit_directory/$INSTANCE.service"
    systemd-analyze verify "$temporary_unit_directory/$INSTANCE.service"
    install -m 0644 "$temporary_unit_directory/$INSTANCE.service" "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl enable "$INSTANCE.service"
}

check_local_readiness() {
    systemd-run --quiet --wait --pipe --collect --property=Type=exec \
        "--unit=$INSTANCE-ready-$$-$RANDOM" "--property=User=$INSTANCE" \
        "--property=Group=$INSTANCE" "--property=WorkingDirectory=$DATA_DIRECTORY" \
        "--property=EnvironmentFile=$CONFIG_FILE" \
        "$RELEASE_DIRECTORY/.venv/bin/python" "$RELEASE_DIRECTORY/deploy/install/check_ready.py" "$@"
}

install_local() {
    prepare_local_account
    prepare_release
    prepare_local_environment
    # Do not interrupt an existing service until the replacement is fully built.
    # Stop only our own unit before probing the port, including ordinary restarts.
    # A repeated --no-start preserves an already running installation.
    if [[ $UPGRADE == true || $START_APPLICATION == true ]]; then
        if [[ -f $SERVICE_FILE ]]; then systemctl stop "$INSTANCE.service"; fi
        check_local_readiness --check-bind
    elif [[ ! -f $SERVICE_FILE ]]; then
        check_local_readiness --check-bind
    fi
    if [[ ! -f $INITIALIZED_FILE ]]; then
        run_local_command init --admin "$ADMIN_USERNAME"
        mark_initialized
    elif [[ $UPGRADE == true ]]; then
        run_local_command migrate
    fi
    run_local_command check
    activate_release
    install_local_service
    if [[ $START_APPLICATION == true ]]; then
        systemctl restart "$INSTANCE.service"
        check_local_readiness
    fi
    inform "Local installation ready: $INSTANCE.service"
    inform "Configuration: $CONFIG_FILE; data: $DATA_DIRECTORY"
    inform "Manage with: systemctl status $INSTANCE; journalctl -u $INSTANCE -f"
}
