#!/bin/bash
# One-time host provisioning for the Feature Factory. Run as root on the VPS,
# from /root/broker-cockpit. Idempotent. See docs/capabilities/feature-factory.md.
#
# Provisions the UNPRIVILEGED builder model: builds run as the system user
# `factory` (no sudo rights, nologin shell, home /home/factory) with a single
# readable credential (/etc/feature-factory.env, root:factory 0640). factory
# has no path into /root — asserted by the self-check at the end.
set -euo pipefail
REPO=/root/broker-cockpit
KEY=$REPO/secrets/feature_runner_key
ENV_FILE=/etc/feature-factory.env
LEGACY_ENV=/root/.feature-factory.env
RUNNER=$REPO/scripts/feature_runner.sh
AUTH=/root/.ssh/authorized_keys
CLAUDE_BIN=/usr/local/bin/claude
FACTORY_HOME=/home/factory

cd "$REPO"
mkdir -p secrets && chmod 700 secrets
chmod +x "$RUNNER"
command -v sudo >/dev/null || { echo "sudo is required (apt-get install -y sudo)"; exit 1; }

# 1. Unprivileged builder user (no sudo, no login shell; invoked only via
#    `sudo -u factory` from the root runner).
if ! id -u factory >/dev/null 2>&1; then
  useradd --system -m -d "$FACTORY_HOME" -s /usr/sbin/nologin factory
fi
mkdir -p "$FACTORY_HOME/features"
chown factory:factory "$FACTORY_HOME" "$FACTORY_HOME/features"
chmod 750 "$FACTORY_HOME"

# 2. Claude CLI at a system path so factory can execute it (/root/.local/bin
#    is unreachable for factory — /root is 0700).
if [ ! -x "$CLAUDE_BIN" ]; then
  echo "Installing Claude CLI to $CLAUDE_BIN…"
  [ -x /root/.local/bin/claude ] || curl -fsSL https://claude.ai/install.sh | bash
  install -m 755 "$(readlink -f /root/.local/bin/claude)" "$CLAUDE_BIN"
fi

# 3. Builder credential — two supported modes:
#    (a) subscription: `claude login` AS THE FACTORY USER; OAuth creds live in
#        /home/factory/.claude/ and builds draw on the operator's Claude plan
#        (hard-capped by the plan — no separate spend limit needed).
#    (b) api-key: ANTHROPIC_API_KEY in /etc/feature-factory.env
#        (root:factory 0640) — requires a spend limit in the Anthropic console.
#    Either way the credential is the ONLY secret the factory user can read.
CRED_FILE=/home/factory/.claude/.credentials.json
if [ -f "$LEGACY_ENV" ] && [ ! -f "$ENV_FILE" ]; then
  mv "$LEGACY_ENV" "$ENV_FILE"
fi
if [ ! -f "$ENV_FILE" ] && ! sudo -u factory test -s "$CRED_FILE"; then
  echo "Choose builder credential mode:"
  echo "  1) subscription (claude login as the factory user — uses your Claude plan credits)"
  echo "  2) api-key (separate API billing; set a console spend limit)"
  read -rp "Mode [1/2]: " mode
  if [ "$mode" = "2" ]; then
    read -rsp "Paste ANTHROPIC_API_KEY for the feature builder: " k; echo
    printf 'ANTHROPIC_API_KEY=%s\n' "$k" > "$ENV_FILE"
  else
    echo "Launching 'claude login' as the factory user — open the URL it prints"
    echo "in your browser, authenticate, and paste the code back here."
    # cwd must be factory-readable: the CLI stats ./.claude/settings.json in
    # its working directory, and setup is typically run from /root/... which
    # the factory user (correctly) cannot traverse -> EACCES settings errors.
    (cd "$FACTORY_HOME" && sudo -Hu factory "$CLAUDE_BIN" login)
    sudo -u factory test -s "$CRED_FILE" || {
      echo "FAIL: no credentials at $CRED_FILE after login"; exit 1; }
  fi
fi
if [ -f "$ENV_FILE" ]; then
  chown root:factory "$ENV_FILE"
  chmod 640 "$ENV_FILE"
fi

# 4. Assert the root-side secret perimeter (defense by construction, not hope).
chmod 700 /root
mkdir -p /root/.ssh && chmod 700 /root/.ssh
if [ -f "$REPO/.env" ]; then
  chown root:root "$REPO/.env"
  chmod 600 "$REPO/.env"
fi
mkdir -p "$REPO/.features" && chmod 700 "$REPO/.features"

# 5. Forced-command SSH key so the worker can only run the runner
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C feature-runner -q
  chmod 600 "$KEY"
fi
LINE="command=\"$RUNNER\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding $(cat "$KEY.pub")"
touch "$AUTH"; chmod 600 "$AUTH"
grep -qF "$RUNNER" "$AUTH" || echo "$LINE" >> "$AUTH"

# 6. host-gateway alias so the worker container can ssh back to the host
grep -q "extra_hosts" compose.yml || echo "NOTE: add 'extra_hosts: [\"host-gateway:host-gateway\"]' to the worker service, or set SSH_DEST to the host's docker0 IP."

# 7. SELF-CHECK: prove the isolation actually holds on THIS host.
echo
echo "=== Feature-factory isolation self-check ==="
fails=0
check() { # check <PASS-if:ok|fail> <description> <cmd...>
  local expect=$1 desc=$2; shift 2
  local got=ok
  "$@" >/dev/null 2>&1 || got=fail
  if [ "$got" = "$expect" ]; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (expected $expect, got $got)"
    fails=$((fails+1))
  fi
}
if [ -f "$REPO/.env" ]; then
  check fail "factory CANNOT read $REPO/.env"          sudo -u factory cat "$REPO/.env"
fi
check fail "factory CANNOT read /root/.ssh/id_ed25519" sudo -u factory cat /root/.ssh/id_ed25519
check fail "factory CANNOT list /root"                  sudo -u factory ls /root
check fail "factory CANNOT list $REPO/secrets"          sudo -u factory ls "$REPO/secrets"
if [ -f "$ENV_FILE" ]; then
  check ok "factory CAN read $ENV_FILE"                 sudo -u factory cat "$ENV_FILE"
else
  check ok "factory CAN read its claude OAuth creds"    sudo -u factory test -s "$CRED_FILE"
fi
check ok   "factory CAN run $CLAUDE_BIN --version"      sudo -Hu factory sh -c "cd '$FACTORY_HOME' && '$CLAUDE_BIN' --version"
# Functional git check: clone → chown → factory git status, as the runner does.
SC_TMP="$FACTORY_HOME/features/.selfcheck-$$"
rm -rf "$SC_TMP"
git clone -q "file://$REPO" "$SC_TMP"
chown -R factory:factory "$SC_TMP"
check ok   "factory CAN run git in a chowned clone"     sudo -Hu factory git -C "$SC_TMP" status
rm -rf "$SC_TMP"
if [ "$fails" -gt 0 ]; then
  echo "SELF-CHECK FAILED ($fails) — do NOT enable the factory until every line is PASS."
  exit 1
fi
echo "SELF-CHECK PASSED."

echo
if [ -f "$ENV_FILE" ]; then
echo "############################################################################"
echo "# REQUIRED BEFORE FIRST ACTIVATION — SPEND LIMIT (api-key mode)            #"
echo "#                                                                          #"
echo "# The host cannot cap Anthropic spend. Set a MONTHLY SPEND LIMIT on the    #"
echo "# builder API key's workspace in the Anthropic Console                     #"
echo "# (console.anthropic.com -> Settings -> Limits) and use a key scoped to a  #"
echo "# dedicated workspace for the factory. Do not enable the Features tab      #"
echo "# until this limit exists.                                                 #"
echo "############################################################################"
else
echo "Builder credential mode: SUBSCRIPTION (claude login as the factory user)."
echo "Spend is hard-capped by your Claude plan's usage limits — no console spend"
echo "limit needed. Builds share your plan's rate limits with your own usage."
fi
echo
echo "Feature Factory host setup complete."
echo "Ensure the worker mounts ./secrets (it already does) and can resolve host-gateway."
echo "Restart the worker: docker compose -f compose.yml -f compose.prod.yml up -d worker"
