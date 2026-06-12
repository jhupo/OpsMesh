#!/usr/bin/env sh
set -eu

if [ "${OPSMESH_RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
