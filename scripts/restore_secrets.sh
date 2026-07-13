#!/usr/bin/env bash
# restore_secrets.sh — pull an encrypted secrets blob from GCS, decrypt it with
# the operator-supplied passphrase, and extract it into a STAGING directory for
# manual review. It deliberately does NOT overwrite the live .env or secrets/:
# you inspect the staged files, then copy them into place yourself.
#
# Counterpart to scripts/backup_secrets.sh. See docs/RESTORE.md.
#
# USAGE (as root, from the repo root on the VPS):
#   scripts/restore_secrets.sh                 # latest blob -> ./secrets-restore-<stamp>/
#   scripts/restore_secrets.sh <blob-name>     # a specific cockpit-secrets-*.tar.gz.gpg
#   scripts/restore_secrets.sh <blob> <dir>    # explicit staging dir
#
# The passphrase is the one you stored in your password manager when you ran
# backup_secrets.sh. It is read interactively and handed to gpg on a pipe —
# never written to disk, never placed in argv.
set -euo pipefail

BLOB="${1:-}"
STAGE="${2:-}"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO"
[ -f ./.env ] || { echo "ERROR: no ./.env in $REPO — run from the repo root on the host." >&2; exit 1; }

if ! command -v gpg >/dev/null 2>&1; then
  echo "gpg not found — installing gnupg (requires root)…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq gnupg
  else
    echo "ERROR: gpg missing and no apt-get to install it." >&2; exit 1
  fi
fi

set -a; . ./.env; set +a
: "${GCS_BUCKET:?GCS_BUCKET must be set in .env}"
: "${GCS_KEY_FILE:?GCS_KEY_FILE must be set in .env}"
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"

# --- pick the blob ----------------------------------------------------------
if [ -z "$BLOB" ]; then
  echo "No blob named — selecting the latest under gs://$GCS_BUCKET/secrets/ …"
  BLOB=$($DC run --rm --no-deps -T backup rclone lsf "${REMOTE}/secrets/" </dev/null 2>/dev/null | sort | tail -1)
  [ -n "$BLOB" ] || { echo "ERROR: no secret blobs found under secrets/." >&2; exit 1; }
fi
echo "Restoring blob: $BLOB"

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
STAGE="${STAGE:-$REPO/secrets-restore-$STAMP}"
[ -e "$STAGE" ] && { echo "ERROR: staging dir $STAGE already exists — refusing to clobber." >&2; exit 1; }

umask 077
mkdir -p "$STAGE"
TMPDIR_S=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR_S"; unset PASS || true; }
trap cleanup EXIT INT TERM
ENC="$TMPDIR_S/blob.tar.gz.gpg"

# --- download via the backup container's rclone (--no-deps: leave postgres be).
# stdin from /dev/null so `docker compose run` cannot swallow the passphrase that
# the operator (or a pipe) supplies to the `read` prompt below. --------------
$DC run --rm --no-deps -T backup rclone cat "${REMOTE}/secrets/${BLOB}" </dev/null > "$ENC"
[ -s "$ENC" ] || { echo "ERROR: downloaded blob is empty." >&2; exit 1; }

# --- prompt for passphrase, decrypt, extract into staging -------------------
read -rsp "Passphrase (from your password manager): " PASS; echo
[ -n "$PASS" ] || { echo "ERROR: empty passphrase." >&2; exit 1; }

# Decrypt to stdout (passphrase on fd 0) and pipe straight into tar -> staging.
printf '%s' "$PASS" | gpg --batch --quiet \
  --pinentry-mode loopback --passphrase-fd 0 --decrypt "$ENC" \
  | tar -xzf - -C "$STAGE"
unset PASS

echo
echo "Decrypted and extracted into: $STAGE"
echo "Contents:"
( cd "$STAGE" && find . -type f | sed 's/^/  /' )
echo
echo "NEXT STEPS (manual, so you can review before touching live files):"
echo "  1. Inspect the staged files above. Do NOT print their contents into any"
echo "     shared log or chat."
echo "  2. Copy into place, e.g.:"
echo "       cp $STAGE/.env            $REPO/.env"
echo "       cp -a $STAGE/secrets/.    $REPO/secrets/"
echo "       # extras (if present): etc/feature-factory.env, root/.ssh/*, root/.claude/*"
echo "  3. Fix ownership + perms:"
echo "       chown root:root $REPO/.env && chmod 600 $REPO/.env"
echo "       chmod 700 $REPO/secrets && chmod 600 $REPO/secrets/*"
echo "       chown root:factory /etc/feature-factory.env 2>/dev/null && chmod 640 /etc/feature-factory.env 2>/dev/null || true"
echo "  4. Restart affected containers, e.g. the worker (uses secrets/):"
echo "       docker compose -f compose.yml -f compose.prod.yml up -d --no-deps worker"
echo "  5. When satisfied, remove the staging dir: rm -rf $STAGE"
