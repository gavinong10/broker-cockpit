#!/bin/sh
set -eu
# Single backup run. Exits NON-ZERO on any failure so the scheduler loop
# (entrypoint.sh) can detect it and alert Discord. Do not swallow errors here.
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
RAW="/tmp/cockpit-${STAMP}.sql"
FILE="${RAW}.gz"
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"

# Dump to a file first rather than `pg_dump | gzip`: in a pipeline the exit
# status is gzip's (which succeeds on empty input), so a failed pg_dump would
# silently upload a truncated dump and exit 0. Writing to a file makes
# `set -e` catch a pg_dump failure directly.
pg_dump -h postgres -U "$POSTGRES_USER" "$POSTGRES_DB" > "$RAW"
gzip -f "$RAW"   # -> $FILE, removes $RAW
rclone copyto "$FILE" "${REMOTE}/cockpit-${STAMP}.sql.gz"
rclone delete "${REMOTE}/" --min-age 30d
rm -f "$FILE"
echo "backup ok: cockpit-${STAMP}.sql.gz"
