#!/bin/sh
set -eu
LATEST=$(docker compose run --rm backup rclone lsf ":b2:${B2_BUCKET}/" \
  --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY" | sort | tail -1)
docker compose run --rm backup sh -c \
  "rclone cat ':b2:${B2_BUCKET}/${LATEST}' --b2-account $B2_KEY_ID --b2-key $B2_APP_KEY" \
  > /tmp/drill.sql.gz
# DROP/CREATE DATABASE must be separate psql -c calls: a multi-statement -c
# runs as one implicit transaction, and DROP DATABASE is disallowed inside one.
docker compose exec -T postgres psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS drill;"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -c "CREATE DATABASE drill;"
gunzip -c /tmp/drill.sql.gz | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d drill
LIVE=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM users")
DRILL=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d drill -tAc "SELECT count(*) FROM users")
[ "$LIVE" = "$DRILL" ] && echo "RESTORE DRILL PASS (users: $LIVE)" || { echo "FAIL: $LIVE != $DRILL"; exit 1; }
