#!/bin/sh
set -eu
# Run from the repo root. Loads .env so the drill is a single command,
# and works with either the compose plugin or standalone docker-compose.
set -a; . ./.env; set +a
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE}:${GCS_BUCKET}"
LATEST=$($DC run --rm backup rclone lsf "${REMOTE}/" | sort | tail -1)
$DC run --rm backup rclone cat "${REMOTE}/${LATEST}" > /tmp/drill.sql.gz
# DROP/CREATE DATABASE must be separate psql -c calls: a multi-statement -c
# runs as one implicit transaction, and DROP DATABASE is disallowed inside one.
$DC exec -T postgres psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS drill;"
$DC exec -T postgres psql -U "$POSTGRES_USER" -c "CREATE DATABASE drill;"
gunzip -c /tmp/drill.sql.gz | $DC exec -T postgres psql -U "$POSTGRES_USER" -d drill
LIVE=$($DC exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM users")
DRILL=$($DC exec -T postgres psql -U "$POSTGRES_USER" -d drill -tAc "SELECT count(*) FROM users")
[ "$LIVE" = "$DRILL" ] && echo "RESTORE DRILL PASS (users: $LIVE)" || { echo "FAIL: $LIVE != $DRILL"; exit 1; }
