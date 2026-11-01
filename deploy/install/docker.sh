#!/usr/bin/env bash
# Explicit Docker Compose deployment. Docker installation itself is a prerequisite.

prepare_docker_configuration() {
    if [[ ! -f $CONFIG_FILE ]]; then
        local postgres_password
        postgres_password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
        (umask 077; cat > "$CONFIG_FILE" <<CONFIG
# Preserved on upgrades. Docker credentials are unique to this installation.
OPSMESH_ENV_FILE=$CONFIG_FILE
OPSMESH_IMAGE=$INSTANCE:local
OPSMESH_BIND_HOST=127.0.0.1
OPSMESH_PORT=$PORT
OPSMESH_POSTGRES_PASSWORD=$postgres_password
OPSMESH_DATABASE_URL=postgresql+asyncpg://opsmesh:$postgres_password@postgres:5432/opsmesh
OPSMESH_REDIS_URL=redis://redis:6379/0
OPSMESH_RUN_WORKER_MODE=inline
OPSMESH_TRACING_ENABLED=false
# OPENAI_API_KEY=
CONFIG
        )
    fi
    chmod 0600 "$CONFIG_FILE"
    export OPSMESH_ENV_FILE=$CONFIG_FILE
}

compose() {
    docker compose --project-name "$INSTANCE" --env-file "$CONFIG_FILE" \
        --file "$RELEASE_DIRECTORY/deploy/compose.yaml" "$@"
}

install_docker() {
    prepare_docker_configuration
    prepare_release
    if [[ $REUSE_RELEASE == false ]]; then compose build api worker; fi
    if [[ $UPGRADE == true ]]; then compose stop api worker; fi
    # The database and Redis start first; no application worker sees an uninitialized schema.
    compose up -d --wait --wait-timeout 120 postgres redis
    if [[ ! -f $INITIALIZED_FILE ]]; then
        OPSMESH_ADMIN_PASSWORD=$ADMINISTRATOR_PASSWORD \
            compose run --rm --no-deps -T -e OPSMESH_ADMIN_PASSWORD api init --admin "$ADMIN_USERNAME"
        mark_initialized
    elif [[ $UPGRADE == true ]]; then
        compose run --rm --no-deps -T api migrate
    fi
    compose run --rm --no-deps -T api check
    activate_release
    if [[ $START_APPLICATION == true ]]; then
        compose up -d --wait --wait-timeout 120 api worker
        compose exec -T api python /opt/opsmesh/deploy/install/check_ready.py
    fi
    inform "Docker installation ready: Compose project $INSTANCE"
    inform "Configuration: $CONFIG_FILE; application/database data remain in named volumes"
    inform "Manage with: docker compose -p $INSTANCE --env-file $CONFIG_FILE -f $CURRENT_RELEASE/deploy/compose.yaml ps"
}
