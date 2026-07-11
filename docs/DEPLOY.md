# DEPLOY.md — VPS deployment runbook (Phase 0)

Deploys broker-cockpit to the Ubuntu 24.04 VPS from Task 0, serving
`https://$COCKPIT_DOMAIN` (e.g. `cockpit.<your-domain>`) via Caddy with
automatic Let's Encrypt TLS. In prod, only caddy publishes host ports
(80/443); `web`, `worker`, `ib-gateway`, `postgres`, and `backup` stay on
the internal docker network.

Prerequisites (Task 0, already done): VPS provisioned, DNS A record for
`cockpit.<your-domain>` pointing at its IP, Google OAuth client created,
IBKR paper credentials in the password manager, B2 bucket + key, Discord
webhook.

For disaster recovery and the quarterly restore drill, see
[docs/RESTORE.md](RESTORE.md).

## 1. Firewall — SSH + HTTP(S) only

As root on the VPS:

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status   # expect exactly 22, 80, 443 allowed
```

No other ports. Postgres (5432) and the worker (8000) must never be
reachable from the internet — they are not published by the prod overlay,
and ufw is the backstop.

## 2. Install Docker (includes the compose plugin)

```bash
curl -fsSL https://get.docker.com | sh
docker compose version   # sanity check the plugin is present
```

## 3. Clone the repo

```bash
git clone <repo-remote-url> broker-cockpit && cd broker-cockpit
```

> **Updating an existing deployment:** use a plain `git pull` (merge pull),
> NOT `git pull --ff-only` — feature-factory accepts create merge commits
> directly on the VPS's main, so its history is legitimately ahead of origin
> between pushes and ff-only will refuse.

## 4. Create `.env` from the password manager

```bash
cp .env.example .env
# fill every variable with the values stored in the password manager
```

Prod-specific values:

- `COCKPIT_DOMAIN=cockpit.<your-domain>` — Caddy serves and provisions TLS
  for exactly this hostname.
- `AUTH_URL=https://$COCKPIT_DOMAIN` — **must** be the https prod URL
  (replace the local `http://localhost:3000`), or Auth.js will generate
  wrong callback URLs and Google login will fail.

> **IB credentials: paper first, live later.** Fill `IB_USER`/`IB_PASSWORD`
> with the **paper** account credentials and keep `IB_TRADING_MODE=paper`.
> The initial deployment (and the whole Phase 0 soak) runs against paper.
> Swapping to the live headless secondary user (the SLS-opted-out,
> trading-permissions-only user from Task 0) is a **separate, deliberate
> step later**: change `IB_USER`/`IB_PASSWORD` to that user and
> `IB_TRADING_MODE=live`, then recreate `ib-gateway` and `worker`. Do not
> do this as part of first deployment.

## 5. Update the Google OAuth client for the prod domain

In console.cloud.google.com → project `broker-cockpit` → Credentials →
your OAuth 2.0 Client ID → Authorized redirect URIs, ensure this entry
exists (alongside the localhost one):

```
https://cockpit.<your-domain>/api/auth/callback/google
```

Save. Changes can take a few minutes to propagate.

## 6. Bring up the stack (prod overlay)

```bash
docker compose -f compose.yml -f compose.prod.yml up -d --build
```

The overlay removes web's host port and adds caddy on 80/443. Caddy
obtains the TLS certificate from Let's Encrypt on first request —
requires the DNS A record to already resolve to this VPS.

## 7. Verify

1. **Login page over TLS** (from your laptop, not the VPS):

   ```bash
   curl -s https://$COCKPIT_DOMAIN/login | grep -i google
   ```

   Expect the login markup containing the Google sign-in button.

2. **Full browser round-trip**: open `https://$COCKPIT_DOMAIN` → redirected
   to `/login` → sign in with the `OWNER_EMAIL` Google account → home page
   shows `Signed in as gavinong10@gmail.com (owner)`. A non-allowlisted
   Google account must land on `/denied`.

3. **Worker health** (in-container; the worker is intentionally not
   reachable from outside):

   ```bash
   docker compose exec worker uv run python -c \
     "import httpx; print(httpx.get('http://localhost:8000/health').json())"
   ```

   Expect `{'db': 'ok', 'gateway': 'connected'}`. If `gateway` is `down`,
   check `docker compose logs -f ib-gateway` for `IBC: Login has completed`.

4. **No stray listeners**: `ss -tlnp | grep -v 127.0.0.1` on the VPS should
   show only docker-proxy on 80/443 (plus sshd on 22).

## Phase 0 exit

Placeholder — soak evidence is recorded here after Task 8: seven
consecutive daily heartbeats with `gateway: connected` and no manual
intervention (screenshot of the Discord embeds or an `audit_log` query for
`system.heartbeat` rows), after which Phase 0 is marked complete in the
spec.
