#!/usr/bin/env sh
set -eu

umask 0007
exec "$@"
