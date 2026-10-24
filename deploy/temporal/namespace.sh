#!/bin/sh
set -eu
namespace=${TEMPORAL_NAMESPACE:-default}
attempt=0
until temporal operator cluster health --address temporal:7233; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || exit 1
  sleep 5
done
attempt=0
until temporal operator namespace describe --address temporal:7233 -n "$namespace" >/dev/null 2>&1; do
  temporal operator namespace create --address temporal:7233 -n "$namespace" --retention 168h && exit 0
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || exit 1
  sleep 5
done
