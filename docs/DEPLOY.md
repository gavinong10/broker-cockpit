# DEPLOY.md — VPS deployment & update runbook

broker-cockpit runs on an Ubuntu 24.04 VPS (`204.168.169.27`), serving
`https://cockpit.gavinong.org` via Caddy with automatic TLS. In prod, only
caddy publishes host ports (80/443); `web`, `worker`, `ib-gateway`, `postgres`,
and `backup` stay on the internal docker network (`compose.prod.yml` resets
every other published port — see the ufw note below).

- **First-time / disaster recovery:** see [docs/RESTORE.md](RESTORE.md) for the
  full cold-restore order (provision → secrets → DB → compose up → manual
  checklist). This file covers the **ongoing deploy/update** workflow and a
  from-scratch first deploy on an already-provisioned host.
- **Host provisioning** (Docker, ufw, fail2ban, sshd hardening, the `edge`
  network, the remote-control unit, feature-factory) is a single idempotent
  script: `scripts/provision_host.sh` (`--check` to preview). Do not hand-run
  the individual steps below unless the script is unavailable — they are kept
  for reference and match exactly what the script ensures.

> **Compose binary:** the VPS uses the **`docker compose` plugin (v2)**; the
> local colima dev Mac uses the standalone **`docker-compose`**. Don't mix them.
> Prod commands: `docker compose -f compose.yml -f compose.prod.yml ...`.

---

## First deploy on a provisioned host

Assumes `provision_host.sh` has run and `.env` + `secrets/` are in place
(restored per RESTORE.md, or filled from the password manager).

1. **Firewall** (idempotent; provision_host.sh already did this):

   ```bash
   ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
   ufw status   # expect exactly 22, 80, 443
   ```

   Postgres (5432) and the worker (8000) must never be internet-reachable —
   they are not published by the prod overlay, and ufw is the backstop. Note
   ufw does NOT protect docker-published ports (iptables runs ahead of it),
   which is why `compose.prod.yml` resets them.

2. **Docker** (idempotent): `curl -fsSL https://get.docker.com | sh` — bundles
   the compose plugin. `docker compose version` to confirm.

3. **`.env`** must have prod-specific values:
   - `COCKPIT_DOMAIN=cockpit.<your-domain>` — Caddy serves + provisions TLS for
     exactly this hostname (HTTP-01, grey-cloud DNS).
   - `AUTH_URL=https://$COCKPIT_DOMAIN` — **must** be the https prod URL (not
     `http://localhost:3000`), or Auth.js generates wrong callback URLs and
     Google login fails.
   - `BETA_DOMAIN` + `CLOUDFLARE_API_TOKEN` (DNS-edit scope) — only if the beta
     stack is in use; caddy uses the token for the `*.beta` wildcard cert via
     DNS-01. Least privilege: caddy gets only these three env vars, never the
     full `.env`.
   - **IB: paper first.** Fill `IB_USER`/`IB_PASSWORD` with the **paper**
     account and keep `IB_TRADING_MODE=paper`. The live headless secondary-user
     swap is a separate deliberate step (see below), not part of first deploy.

4. **Google OAuth redirect URI** (console.cloud.google.com → project
   `broker-cockpit-8496` → your OAuth 2.0 Client ID → Authorized redirect URIs)
   must include, alongside the localhost one:

   ```
   https://cockpit.<your-domain>/api/auth/callback/google
   ```

5. **Bring up the stack:**

   ```bash
   docker compose -f compose.yml -f compose.prod.yml up -d --build
   ```

   Caddy obtains the TLS cert from Let's Encrypt on first request — requires the
   DNS A record to already resolve to this VPS, grey-clouded (proxy OFF).

---

## Updating an existing deployment

From the **main checkout on `main`** on the VPS (never from a worktree, never
from a feature-factory sandbox):

```bash
cd /root/broker-cockpit
git pull      # MERGE pull — see rule below
```

> **`git pull` is a MERGE pull, NOT `git pull --ff-only`.** The feature-factory
> lands accept merges directly on this host's `main`, so its history is
> legitimately ahead of origin between pushes; `--ff-only` will refuse.

Then rebuild only what changed:

- **Web-only deploy:**

  ```bash
  docker compose -f compose.yml -f compose.prod.yml up -d --build --no-deps web
  ```

  **`--no-deps` is critical** — a plain `up -d --build web` also recreates the
  `worker` container, which KILLS any long-running job in the worker (this has
  interrupted the history backfill; `scripts/backfill_snapshots.py` is not yet
  resumable). Only omit `--no-deps` to rebuild the worker on purpose.

- **Worker deploy:** `... up -d --build worker` — but first confirm no
  long-running script is running inside it (`pgrep -af backfill_snapshots`).

- **Caddy / infra change:** `... up -d --build caddy` (rebuilds the
  cloudflare-DNS-plugin image).

Health check after any deploy:

```bash
docker compose -f compose.yml -f compose.prod.yml exec -T worker \
  uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"
```

Expect `{'db': 'ok', 'gateway': ...}` — `disabled` while IBKR is off
(`IB_ENABLED=false`), `connected` once the gateway is up. If `down`, check
`docker compose ... logs -f ib-gateway` for `IBC: Login has completed`.

---

## Pushing `.env` / secret changes to the VPS

`.env` is gitignored; `.env.vps` is a local gitignored working copy of the real
VPS `.env`. To change a secret: edit `.env.vps` locally, `scp` it to
`/root/broker-cockpit/.env` on the VPS, restart the affected containers, **then
run `scripts/backup_secrets.sh` on the VPS** so the encrypted GCS secrets backup
reflects the change (see RESTORE.md).

---

## The live IB swap (paper → live) — deliberate, later

Change `IB_USER`/`IB_PASSWORD` to the live headless secondary user
(trading-permissions-only, SLS-opted-out) and `IB_TRADING_MODE=live`, then
recreate only the broker path:

```bash
docker compose -f compose.yml -f compose.prod.yml up -d ib-gateway worker
```

Do not do this as part of a routine deploy. Do not conflate paper and live
credentials.

---

## Two Claude tiers on the box (see docs/host/root-claude-md.md)

- **Root remote-control** (`claude-remote-control.service`): trusted operator
  access for deploys/debugging. Managed by systemd; needs a claude.ai login.
- **Feature factory** (`factory` user): a sandboxed, credential-less, code-only
  builder gated by diff-review before accept. Set up via
  `scripts/setup_feature_factory.sh`; runner is `scripts/feature_runner.sh`.
  Never confuse its containment with root's access.

## Verify (post-deploy)

1. **Login over TLS** (from your laptop): `curl -s https://$COCKPIT_DOMAIN/login | grep -i google`.
2. **Full round-trip:** open `https://$COCKPIT_DOMAIN` → `/login` → sign in with
   the `OWNER_EMAIL` account → home shows `Signed in as … (owner)`; a
   non-allowlisted account lands on `/denied`.
3. **No stray listeners:** `ss -tlnp | grep -v 127.0.0.1` on the VPS should show
   only docker-proxy on 80/443 plus sshd on 22.
