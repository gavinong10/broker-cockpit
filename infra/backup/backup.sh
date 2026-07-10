#!/bin/sh
set -eu
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
FILE="/tmp/cockpit-${STAMP}.sql.gz"
pg_dump -h postgres -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$FILE"
rclone copyto "$FILE" ":b2:${B2_BUCKET}/cockpit-${STAMP}.sql.gz" \
  --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY"
rclone delete ":b2:${B2_BUCKET}/" --min-age 30d \
  --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY"
rm -f "$FILE"
echo "backup ok: cockpit-${STAMP}.sql.gz"
