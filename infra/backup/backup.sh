#!/bin/sh
set -eu
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
FILE="/tmp/cockpit-${STAMP}.sql.gz"
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"
pg_dump -h postgres -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$FILE"
rclone copyto "$FILE" "${REMOTE}/cockpit-${STAMP}.sql.gz"
rclone delete "${REMOTE}/" --min-age 30d
rm -f "$FILE"
echo "backup ok: cockpit-${STAMP}.sql.gz"
