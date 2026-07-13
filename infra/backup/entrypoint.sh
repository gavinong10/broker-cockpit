#!/bin/sh
# PID 1-friendly loop scheduler. Replaces busybox `crond -f`, which crashes in
# this container runtime with "setpgid: Operation not permitted" (no restart
# policy meant backups then silently stopped). This is a plain foreground
# shell loop: compute seconds until the next 08:15 UTC, sleep, run the backup,
# self-check freshness, and (Sundays) run a restore drill. No cron daemon, no
# setpgid, no forking off PID 1.
set -u

STATE_DIR=/var/lib/backup
mkdir -p "$STATE_DIR"
HEARTBEAT="$STATE_DIR/last-run"

BACKUP_TIME="${BACKUP_TIME:-08:15}"   # daily target, UTC
STALE_HOURS="${STALE_HOURS:-26}"      # alert if newest backup older than this
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"

# ---------------------------------------------------------------------------
# Discord alerting (best-effort; never fatal to the loop).
# ---------------------------------------------------------------------------
json_escape() {
    esc=$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    printf '"%s"' "$esc"
}

notify() {
    [ -n "${DISCORD_WEBHOOK_URL:-}" ] || { echo "notify (no webhook set): $1"; return 0; }
    code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' \
        -H "Content-Type: application/json" \
        -d "{\"content\": $(json_escape "$1")}" \
        "$DISCORD_WEBHOOK_URL" 2>/dev/null || echo "000")
    echo "notify -> HTTP $code: $1"
}

fmt_epoch() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "epoch $1"; }

# ---------------------------------------------------------------------------
# Freshness self-check: newest object in the bucket must be < STALE_HOURS old.
# Uses the backup filename stamp (cockpit-YYYY-MM-DDThhmmssZ.sql.gz) as the
# authoritative timestamp, so it works even if GCS ModTime formatting varies.
# Returns 0 = fresh, 1 = stale/unreadable (and alerts in the stale case).
# ---------------------------------------------------------------------------
newest_backup_epoch() {
    name=$(rclone lsf "${REMOTE}/" 2>/dev/null | grep '\.sql\.gz$' | sort | tail -1)
    [ -n "$name" ] || return 1
    stamp=${name#cockpit-}
    stamp=${stamp%.sql.gz}                       # YYYY-MM-DDThhmmssZ
    d=${stamp%%T*}                               # YYYY-MM-DD
    t=${stamp#*T}; t=${t%Z}                      # hhmmss
    hh=$(printf '%s' "$t" | cut -c1-2)
    mm=$(printf '%s' "$t" | cut -c3-4)
    ss=$(printf '%s' "$t" | cut -c5-6)
    date -u -d "$d $hh:$mm:$ss UTC" +%s 2>/dev/null
}

check_staleness() {
    now=$(date -u +%s)
    epoch=$(newest_backup_epoch) || {
        notify "⚠️ No successful broker-cockpit backup in >${STALE_HOURS}h — backups may be broken (bucket empty or unreadable)"
        return 1
    }
    age_h=$(( (now - epoch) / 3600 ))
    if [ "$age_h" -ge "$STALE_HOURS" ]; then
        notify "⚠️ No successful broker-cockpit backup in >${STALE_HOURS}h — backups may be broken (newest object is ${age_h}h old)"
        return 1
    fi
    echo "staleness ok: newest backup is ${age_h}h old (threshold ${STALE_HOURS}h)"
    return 0
}

# ---------------------------------------------------------------------------
# One backup attempt + heartbeat + failure alert.
# ---------------------------------------------------------------------------
run_backup() {
    echo "=== backup run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    if /usr/local/bin/backup.sh; then
        date -u +%Y-%m-%dT%H:%M:%SZ > "${HEARTBEAT}.ok"
        echo "backup succeeded"
        return 0
    fi
    rc=$?
    date -u +%Y-%m-%dT%H:%M:%SZ > "${HEARTBEAT}.fail"
    notify "❌ broker-cockpit backup FAILED (backup.sh exit ${rc}) — check the backup container logs now"
    return 1
}

run_drill() {
    echo "=== weekly restore drill $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    if /usr/local/bin/drill.sh; then
        echo "restore drill passed"
        return 0
    fi
    notify "❌ broker-cockpit weekly restore drill FAILED — the latest GCS backup may not be restorable. See backup container logs."
    return 1
}

# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------
echo "backup scheduler starting: daily ${BACKUP_TIME} UTC, stale threshold ${STALE_HOURS}h, bucket ${GCS_BUCKET}"

# Optional immediate run (verification / manual trigger).
if [ "${RUN_NOW:-0}" = "1" ]; then
    echo "RUN_NOW=1 -> running backup immediately"
    run_backup || true
    check_staleness || true
fi

while true; do
    now=$(date -u +%s)
    target=$(date -u -d "$(date -u +%Y-%m-%d) ${BACKUP_TIME}:00 UTC" +%s)
    [ "$target" -gt "$now" ] || target=$((target + 86400))
    secs=$((target - now))
    echo "next backup at $(fmt_epoch "$target") (sleeping ${secs}s)"
    sleep "$secs"

    run_backup || true      # alerts on failure internally
    check_staleness || true # alerts on staleness internally

    # Weekly restore drill on Sundays (date -u +%u: 7 = Sunday).
    if [ "$(date -u +%u)" = "7" ]; then
        run_drill || true
    fi
done
