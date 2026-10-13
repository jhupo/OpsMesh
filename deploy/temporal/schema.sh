#!/bin/sh
set -eu
for database in temporal temporal_visibility; do
  # PostgreSQL initializes databases once. Native setup preserves an existing higher version.
  temporal-sql-tool --plugin postgres12 --ep temporal-postgres -u temporal -p 5432 \
    --db "$database" setup-schema -v 0.0
  if [ "$database" = temporal ]; then schema=temporal; else schema=visibility; fi
  temporal-sql-tool --plugin postgres12 --ep temporal-postgres -u temporal -p 5432 \
    --db "$database" update-schema -d "/etc/temporal/schema/postgresql/v12/$schema/versioned"
done
