#!/usr/bin/env bash
# provision_host.sh — bring a bare Ubuntu 24.04 box toward the broker-cockpit
# prod stack, AND run as a safe no-op on the ALREADY-provisioned live box.
#
# DESIGN PRINCIPLES (this touches a live money-connected host):
#   * IDEMPOTENT: every step is guarded by an existence/content check. Re-running
#     on a fully provisioned box changes nothing and exits 0.
#   * NON-DESTRUCTIVE: it will NEVER overwrite an existing .env or secrets/,
#     regenerate existing SSH keys, restart running app containers, or touch the
#     database. Those are out of scope (see restore_secrets.sh + docs/RESTORE.md).
#   * --check MODE: reports what it WOULD do without changing anything. Use this
#     to verify no-op safety on the live box before ever running apply mode.
#
# It provisions/ensures: apt basics, Docker + compose plugin, ufw (22/80/443),
# fail2ban + sshd jail, sshd hardening drop-in, the external `edge` docker
# network, the claude-remote-control systemd unit, the feature-factory host
# setup, and /root/.claude/CLAUDE.md.
#
# It does NOT (and cannot) do the external/manual recovery steps — GitHub deploy
# keys, Cloudflare DNS, Google OAuth, GCP project/bucket/service-account, IBKR,
# claude.ai logins. Those are the "external/manual recovery checklist" in
# docs/RESTORE.md.
#
# USAGE (as root):
#   scripts/provision_host.sh --check    # report only, change nothing
#   scripts/provision_host.sh            # apply
set -euo pipefail

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root." >&2; exit 1
fi

CHANGES=0   # count of steps that need (or made) a change

# ok       — already in desired state
# would    — needs a change; in --check we only report, in apply we run $2
# Reports uniformly, increments CHANGES when action is needed.
report_ok()    { printf '  [ok]    %s\n' "$1"; }
report_apply() { CHANGES=$((CHANGES+1)); if [ "$CHECK" -eq 1 ]; then printf '  [would] %s\n' "$1"; else printf '  [apply] %s\n' "$1"; fi; }

# run <desc> <cmd...> : in apply mode run the command; in check mode skip it.
run() { local desc=$1; shift; report_apply "$desc"; [ "$CHECK" -eq 1 ] || "$@"; }

# ensure_file <path> <mode> <owner> — write $DESIRED (a global) to <path> if the
# content differs, then fix mode/owner. Sets nothing in check mode.
# Compare with a trailing newline: our write path is `printf '%s\n'` and the
# $DESIRED heredocs are captured via `read -d ''` which strips the final newline.
file_matches() { [ -f "$1" ] && printf '%s\n' "$2" | cmp -s - "$1"; }

echo "=== broker-cockpit host provisioning ($([ "$CHECK" -eq 1 ] && echo CHECK-ONLY || echo APPLY)) ==="

# --- 1. apt basics ----------------------------------------------------------
echo "[1] apt base packages"
PKGS="ca-certificates curl gnupg ufw fail2ban"
MISSING=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
if [ -n "$MISSING" ]; then
  run "install:$MISSING" sh -c "apt-get update -qq && apt-get install -y -qq $MISSING"
else
  report_ok "all base packages present ($PKGS)"
fi

# --- 2. Docker + compose plugin --------------------------------------------
echo "[2] Docker engine + compose plugin"
if command -v docker >/dev/null 2>&1; then
  report_ok "docker present ($(docker --version 2>/dev/null))"
else
  run "install docker via get.docker.com" sh -c "curl -fsSL https://get.docker.com | sh"
fi
if docker compose version >/dev/null 2>&1; then
  report_ok "compose plugin present ($(docker compose version 2>/dev/null | head -1))"
else
  report_apply "compose plugin MISSING — get.docker.com bundles it; re-run after docker install"
fi

# --- 3. ufw: allow 22 FIRST, then 80/443, then enable ----------------------
echo "[3] ufw firewall (22/80/443)"
ufw_has() { ufw status 2>/dev/null | grep -qE "^$1/tcp\s"; }
for port in 22 80 443; do
  if ufw_has "$port"; then
    report_ok "ufw allows $port/tcp"
  else
    run "ufw allow $port/tcp" ufw allow "$port/tcp"
  fi
done
if ufw status 2>/dev/null | grep -q "Status: active"; then
  report_ok "ufw is active"
else
  run "ufw --force enable" ufw --force enable
fi

# --- 4. fail2ban + sshd jail -----------------------------------------------
echo "[4] fail2ban sshd jail"
JAIL=/etc/fail2ban/jail.d/sshd.local
read -r -d '' JAIL_WANT <<'EOF' || true
[sshd]
enabled = true
port = 22
maxretry = 5
findtime = 10m
bantime = 1h
EOF
if file_matches "$JAIL" "$JAIL_WANT"; then
  report_ok "$JAIL matches"
else
  run "write $JAIL + reload fail2ban" sh -c "printf '%s\n' \"\$0\" > '$JAIL' && systemctl reload-or-restart fail2ban" "$JAIL_WANT"
fi
if systemctl is-enabled fail2ban >/dev/null 2>&1 && systemctl is-active fail2ban >/dev/null 2>&1; then
  report_ok "fail2ban enabled + active"
else
  run "enable + start fail2ban" systemctl enable --now fail2ban
fi

# --- 5. sshd hardening drop-in ---------------------------------------------
echo "[5] sshd hardening drop-in"
HARDEN=/etc/ssh/sshd_config.d/99-hardening.conf
read -r -d '' HARDEN_WANT <<'EOF' || true
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
if file_matches "$HARDEN" "$HARDEN_WANT"; then
  report_ok "$HARDEN matches"
else
  # Validate config before reloading so a bad drop-in can never lock us out.
  run "write $HARDEN + reload ssh" sh -c "printf '%s\n' \"\$0\" > '$HARDEN' && sshd -t && systemctl reload ssh" "$HARDEN_WANT"
fi

# --- 6. external `edge` docker network -------------------------------------
echo "[6] docker network 'edge' (external, used by compose.prod.yml)"
if docker network inspect edge >/dev/null 2>&1; then
  report_ok "docker network 'edge' exists"
else
  run "docker network create edge" docker network create edge
fi

# --- 7. claude-remote-control systemd unit ---------------------------------
echo "[7] claude-remote-control.service"
UNIT=/etc/systemd/system/claude-remote-control.service
UNIT_SRC="$REPO/docs/host/claude-remote-control.service"
if [ ! -f "$UNIT_SRC" ]; then
  report_apply "MISSING source $UNIT_SRC — cannot install unit"
elif [ -f "$UNIT" ] && cmp -s "$UNIT_SRC" "$UNIT"; then
  report_ok "$UNIT matches committed copy"
  if systemctl is-enabled claude-remote-control >/dev/null 2>&1; then
    report_ok "claude-remote-control enabled"
  else
    run "enable claude-remote-control" systemctl enable claude-remote-control
  fi
else
  # Install/refresh the unit + enable. We do NOT restart a running instance here
  # (avoid interrupting an active remote-control session); enable is safe.
  run "install $UNIT + daemon-reload + enable" sh -c "install -m 644 '$UNIT_SRC' '$UNIT' && systemctl daemon-reload && systemctl enable claude-remote-control"
fi

# --- 8. feature-factory host setup (its own idempotent script) -------------
echo "[8] feature-factory host setup"
FF=$REPO/scripts/setup_feature_factory.sh
if [ ! -x "$FF" ]; then
  report_apply "missing or non-executable $FF"
elif id -u factory >/dev/null 2>&1 && { [ -f /etc/feature-factory.env ] || [ -s /home/factory/.claude/.credentials.json ]; }; then
  # Already configured — setup_feature_factory.sh would re-run its self-check
  # (harmless but does a transient temp clone). Treat configured as OK; the
  # operator can run it directly to re-verify isolation.
  report_ok "feature-factory already configured (user 'factory' + credential present)"
else
  run "run setup_feature_factory.sh (interactive on first setup)" "$FF"
fi

# --- 9. /root/.claude/CLAUDE.md --------------------------------------------
echo "[9] /root/.claude/CLAUDE.md (host operator guide)"
ROOT_MD=/root/.claude/CLAUDE.md
ROOT_MD_SRC="$REPO/docs/host/root-claude-md.md"
if [ ! -f "$ROOT_MD_SRC" ]; then
  report_apply "MISSING source $ROOT_MD_SRC"
elif [ -f "$ROOT_MD" ] && cmp -s "$ROOT_MD_SRC" "$ROOT_MD"; then
  report_ok "$ROOT_MD matches committed copy"
else
  run "install $ROOT_MD" sh -c "mkdir -p /root/.claude && install -m 644 '$ROOT_MD_SRC' '$ROOT_MD'"
fi

# --- 10. guardrail assertions (never mutate; just confirm perimeter) --------
echo "[10] secret-perimeter guardrails (read-only assertions)"
if [ -f "$REPO/.env" ]; then
  report_ok ".env present (left untouched — restore via restore_secrets.sh)"
else
  printf '  [note]  .env ABSENT — restore it with scripts/restore_secrets.sh before compose up\n'
fi
if [ -d "$REPO/secrets" ]; then
  report_ok "secrets/ present (left untouched)"
else
  printf '  [note]  secrets/ ABSENT — restore it with scripts/restore_secrets.sh before compose up\n'
fi

echo
if [ "$CHECK" -eq 1 ]; then
  if [ "$CHANGES" -eq 0 ]; then
    echo "CHECK: host is fully provisioned — 0 changes needed."
  else
    echo "CHECK: $CHANGES step(s) would change. Re-run without --check to apply."
  fi
else
  echo "APPLY complete: $CHANGES step(s) changed."
  echo "NOTE: this script does NOT bring up the app stack, restore secrets, or"
  echo "restore the DB. See docs/RESTORE.md for the full cold-restore order."
fi
exit 0
