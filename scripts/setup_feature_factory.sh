#!/bin/bash
# One-time host provisioning for the Feature Factory. Run as root on the VPS,
# from /root/broker-cockpit. Idempotent. See docs/capabilities/feature-factory.md.
set -euo pipefail
REPO=/root/broker-cockpit
KEY=$REPO/secrets/feature_runner_key
ENV_FILE=/root/.feature-factory.env
RUNNER=$REPO/scripts/feature_runner.sh
AUTH=/root/.ssh/authorized_keys

cd "$REPO"
mkdir -p secrets && chmod 700 secrets
chmod +x "$RUNNER"

# 1. Claude CLI for root
if [ ! -x /root/.local/bin/claude ]; then
  echo "Installing Claude CLI…"
  curl -fsSL https://claude.ai/install.sh | bash
fi

# 2. Anthropic credential for the builder (scrubbed env reads only this file)
if [ ! -f "$ENV_FILE" ]; then
  read -rsp "Paste ANTHROPIC_API_KEY for the feature builder: " k; echo
  printf 'ANTHROPIC_API_KEY=%s\n' "$k" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# 3. Forced-command SSH key so the worker can only run the runner
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY" -C feature-runner -q
  chmod 600 "$KEY"
fi
mkdir -p /root/.ssh && chmod 700 /root/.ssh
LINE="command=\"$RUNNER\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding $(cat "$KEY.pub")"
touch "$AUTH"; chmod 600 "$AUTH"
grep -qF "$RUNNER" "$AUTH" || echo "$LINE" >> "$AUTH"

# 4. host-gateway alias so the worker container can ssh back to the host
grep -q "extra_hosts" compose.yml || echo "NOTE: add 'extra_hosts: [\"host-gateway:host-gateway\"]' to the worker service, or set SSH_DEST to the host's docker0 IP."

echo "Feature Factory host setup complete."
echo "Ensure the worker mounts ./secrets (it already does) and can resolve host-gateway."
echo "Restart the worker: docker compose -f compose.yml -f compose.prod.yml up -d worker"
