#!/usr/bin/env bash
# backup_secrets.sh — encrypted off-host backup of the NON-git, NON-recoverable
# secrets (the .env and everything under secrets/) for broker-cockpit.
#
# WHY THIS IS MANUAL (run by hand after secrets change — never cron'd):
#   The archive is encrypted with a symmetric passphrase you type at the prompt.
#   That passphrase lives ONLY in your password manager. Automating this backup
#   would mean storing the decryption passphrase on the box next to the very
#   secrets it protects — which defeats the entire purpose. So: run this after
#   you rotate the Robinhood session, change the DB password, mint a new GCS
#   key, etc. The DB itself has its own automated nightly GCS backup (the
#   `backup` compose service); THIS script is only for the credential set.
#
# CRYPTO: gpg --symmetric --cipher-algo AES256. The passphrase is read
#   interactively, held only in a shell variable, and handed to gpg on a pipe
#   (--passphrase-fd 0). It is NEVER written to disk, NEVER placed on a command
#   line (argv is world-readable via /proc), and NEVER printed.
#
# TRANSPORT: uploaded to gs://$GCS_BUCKET/secrets/ via the existing `backup`
#   compose container's rclone + service-account (the host has no rclone). This
#   reuses the exact REMOTE construction from infra/backup/backup.sh and
#   scripts/restore-drill.sh. DB dumps live at the bucket ROOT; these encrypted
#   secret blobs live under the secrets/ prefix — the two never collide.
#
# USAGE (as root, from the repo root on the VPS):
#   scripts/backup_secrets.sh
#
# Restore with scripts/restore_secrets.sh. See docs/RESTORE.md.
set -euo pipefail

RETAIN=10   # keep the newest N encrypted blobs under secrets/; prune the rest

# --- locate the repo root (this script lives in <repo>/scripts) -------------
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO"

[ -f ./.env ] || { echo "ERROR: no ./.env in $REPO — run from the repo root on the host." >&2; exit 1; }

# --- ensure gpg is available (host had it at 2.4.4; install only if missing) -
if ! command -v gpg >/dev/null 2>&1; then
  echo "gpg not found — installing gnupg (requires root)…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq gnupg
  else
    echo "ERROR: gpg missing and no apt-get to install it." >&2; exit 1
  fi
fi

# --- compose wrapper (VPS has the plugin; local dev has the standalone bin) --
set -a; . ./.env; set +a
: "${GCS_BUCKET:?GCS_BUCKET must be set in .env}"
: "${GCS_KEY_FILE:?GCS_KEY_FILE must be set in .env}"
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"

# --- assemble the secret set ------------------------------------------------
# CRITICAL (always included if present): .env + everything under secrets/.
# JUDICIOUS EXTRAS (included only when they exist): the feature-factory builder
# credential, the root deploy/SSH keypair, and the remote-control login creds.
# REL entries are relative to the repo root; ABS entries are absolute host paths
# (tarred relative to / with the leading slash stripped).
REL_CANDIDATES=( .env secrets )
ABS_CANDIDATES=( /etc/feature-factory.env /root/.ssh/id_ed25519 \
                 /root/.ssh/id_ed25519.pub /root/.claude/.credentials.json )

REL=(); ABS=()
for p in "${REL_CANDIDATES[@]}"; do [ -e "$REPO/$p" ] && REL+=("$p"); done
for p in "${ABS_CANDIDATES[@]}"; do [ -e "$p" ] && ABS+=("${p#/}"); done
[ "${#REL[@]}" -gt 0 ] || { echo "ERROR: nothing to back up (no .env or secrets/)." >&2; exit 1; }

echo "Will back up the following into the encrypted archive:"
for p in "${REL[@]}"; do printf '  %s/%s\n' "$REPO" "$p"; done
for p in "${ABS[@]}"; do printf '  /%s\n' "$p"; done
echo

# --- prompt for the passphrase (never echoed, never stored) -----------------
echo "Enter a STRONG symmetric passphrase. Store it in your password manager NOW —"
echo "it is the ONLY thing that can decrypt this backup and it is written nowhere."
read -rsp "Passphrase: " PASS1; echo
read -rsp "Confirm passphrase: " PASS2; echo
[ -n "$PASS1" ] || { echo "ERROR: empty passphrase." >&2; exit 1; }
[ "$PASS1" = "$PASS2" ] || { echo "ERROR: passphrases do not match." >&2; exit 1; }
unset PASS2

# --- build tar → gpg-encrypt, entirely in a locked-down temp dir ------------
umask 077
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
OBJ="cockpit-secrets-${STAMP}.tar.gz.gpg"
TMPDIR_S=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR_S"; unset PASS1 || true; }
trap cleanup EXIT INT TERM
TAR="$TMPDIR_S/secrets.tar.gz"
ENC="$TMPDIR_S/$OBJ"

# Repo-relative entries under -C "$REPO"; absolute entries under -C / (added
# only when at least one exists, so tar never gets a dangling -C).
if [ "${#ABS[@]}" -gt 0 ]; then
  tar -czf "$TAR" -C "$REPO" "${REL[@]}" -C / "${ABS[@]}"
else
  tar -czf "$TAR" -C "$REPO" "${REL[@]}"
fi

# Encrypt: passphrase on stdin (fd 0), AES256, symmetric. Nothing sensitive in argv.
printf '%s' "$PASS1" | gpg --batch --yes --quiet \
  --pinentry-mode loopback --passphrase-fd 0 \
  --symmetric --cipher-algo AES256 --compress-algo none \
  -o "$ENC" "$TAR"
rm -f "$TAR"
unset PASS1

echo "Encrypted archive built ($(wc -c <"$ENC") bytes). Uploading to gs://$GCS_BUCKET/secrets/$OBJ …"

# --- upload via the backup container's rclone (--no-deps: don't touch postgres) ---
$DC run --rm --no-deps -T backup rclone rcat "${REMOTE}/secrets/${OBJ}" < "$ENC"
echo "Uploaded: gs://$GCS_BUCKET/secrets/$OBJ"

# --- retention: keep newest $RETAIN under secrets/, prune older (NEVER touches
#     the bucket-root DB dumps — strictly scoped to the secrets/ prefix) -------
mapfile -t ALL < <($DC run --rm --no-deps -T backup rclone lsf "${REMOTE}/secrets/" </dev/null 2>/dev/null | sort)
count=${#ALL[@]}
if [ "$count" -gt "$RETAIN" ]; then
  prune=$(( count - RETAIN ))
  echo "Pruning $prune old secret blob(s) (keeping newest $RETAIN):"
  i=0
  while [ "$i" -lt "$prune" ]; do
    name=${ALL[$i]}
    [ -n "$name" ] && { echo "  deleting $name"; $DC run --rm --no-deps -T backup rclone deletefile "${REMOTE}/secrets/${name}" </dev/null; }
    i=$(( i + 1 ))
  done
fi

echo
echo "DONE. Reminder: the passphrase for $OBJ is NOT stored anywhere on this host."
echo "It must be in your password manager or this backup is unrecoverable."
