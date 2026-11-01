#!/usr/bin/env bash
# Install a verified OpsMesh Linux product bundle containing a built wheel.
# Bootstrap credentials must never be expanded into an xtrace log.
set +x
set -Eeuo pipefail

INSTALL_SCRIPT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# These are shipped installer functions, never user configuration files.
source "$INSTALL_SCRIPT_DIRECTORY/install/common.sh"
source "$INSTALL_SCRIPT_DIRECTORY/install/local.sh"
source "$INSTALL_SCRIPT_DIRECTORY/install/docker.sh"

main() {
    parse_arguments "$@"
    unset OPSMESH_ADMIN_PASSWORD ADMINISTRATOR_PASSWORD
    umask 022
    configure_installation_paths
    validate_installation
    prepare_installation_directories
    acquire_installation_lock
    inspect_installation_state
    read_administrator_password
    if [[ $MODE == local ]]; then
        install_local
    else
        install_docker
    fi
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
