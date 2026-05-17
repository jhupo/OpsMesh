#!/usr/bin/env sh
set -eu

if [ "${CHAINCLOUD_RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
