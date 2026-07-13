#!/bin/sh
set -eu
# In-container restore drill. Pulls the latest GCS dump, restores it into a
# scratch database `drill` on the `postgres` service, and sanity-checks it
# against the live DB. Side-effect-free on the real database: it only ever
# CREATE/DROP DATABASE drill and reads (SELECT count) from the live DB.
#
# Row-count semantics: `users` must match live exactly (a stable table; a clean
# recent dump should equal live). Append-only tables like `snapshots` grow
# between the backup and this drill, so requiring exact equality would produce
# false failures — instead we require the restored table to be non-empty and
# not larger than live (drill <= live). We alert only on real breakage:
# restore error, empty table, or an impossible drill > live count.
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"

LATEST=$(rclone lsf "${REMOTE}/" | grep '\.sql\.gz$' | sort | tail -1)
[ -n "$LATEST" ] || { echo "drill: no backup objects in bucket"; exit 1; }
echo "drill: restoring ${LATEST}"
rclone cat "${REMOTE}/${LATEST}" > /tmp/drill.sql.gz

PSQL="psql -v ON_ERROR_STOP=1 -h postgres -U ${POSTGRES_USER}"
$PSQL -c "DROP DATABASE IF EXISTS drill;"
$PSQL -c "CREATE DATABASE drill;"
if ! gunzip -c /tmp/drill.sql.gz | $PSQL -d drill >/dev/null; then
    echo "drill: restore into scratch DB failed"
    $PSQL -c "DROP DATABASE IF EXISTS drill;" || true
    rm -f /tmp/drill.sql.gz
    exit 1
fi
rm -f /tmp/drill.sql.gz

fail=0
# users: exact match expected.
LIVE_U=$($PSQL -tAd "$POSTGRES_DB" -c "SELECT count(*) FROM users")
DRILL_U=$($PSQL -tAd drill -c "SELECT count(*) FROM users")
if [ "$LIVE_U" = "$DRILL_U" ] && [ "$DRILL_U" -gt 0 ]; then
    echo "drill: users OK (live=${LIVE_U} drill=${DRILL_U})"
else
    echo "drill: users FAIL (live=${LIVE_U} drill=${DRILL_U})"
    fail=1
fi
# snapshots: append-only; require non-empty and drill <= live.
LIVE_S=$($PSQL -tAd "$POSTGRES_DB" -c "SELECT count(*) FROM snapshots")
DRILL_S=$($PSQL -tAd drill -c "SELECT count(*) FROM snapshots")
if [ "$DRILL_S" -gt 0 ] && [ "$DRILL_S" -le "$LIVE_S" ]; then
    echo "drill: snapshots OK (live=${LIVE_S} drill=${DRILL_S})"
else
    echo "drill: snapshots FAIL (live=${LIVE_S} drill=${DRILL_S})"
    fail=1
fi

$PSQL -c "DROP DATABASE IF EXISTS drill;"

[ "$fail" = "0" ] || exit 1
echo "RESTORE DRILL PASS (users=${LIVE_U}, snapshots live=${LIVE_S}/drill=${DRILL_S})"
