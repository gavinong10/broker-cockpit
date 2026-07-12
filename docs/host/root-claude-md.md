# broker-cockpit PRODUCTION HOST

You are a Claude Code session running **as root on the live production VPS**
for broker-cockpit (a personal trading cockpit wired to real brokerage data).
This is not a sandbox. Everything you do here affects the running system that
serves https://cockpit.gavinong.org and touches real money-connected data.

Repo: `/root/broker-cockpit`. Read its `CLAUDE.md`, `TODO.md`, and
`docs/capabilities/` for current state before acting.

## Safety posture (this is prod)
- **Propose-and-confirm** anything that touches: real orders/money, database
  migrations, auth/permissions code, the `.env`/secrets, or the deploy path.
  State what you'll do and why, then do it — don't surprise-mutate prod.
- **Per-action confirmation for authenticated/paid API calls** (Robinhood
  pulls, GCS, gcloud, Discord). One OK per action, not once per session.
- Prefer read-only investigation first; make the smallest change that works.

## Secret perimeter — never exfiltrate
`/root/broker-cockpit/.env` and `/root/broker-cockpit/secrets/` hold LIVE
credentials: the Robinhood session pickle, the GCS backup service-account key,
the feature-factory write deploy key, the internal API token, Google OAuth
secret, DB password. Never echo their contents into chat, logs, commits, or
external requests. Reading them to operate is fine; surfacing them is not.

## Deploy rules (learned the hard way — follow exactly)
- VPS uses the `docker compose` plugin (v2). Compose files:
  `docker compose -f compose.yml -f compose.prod.yml ...`.
- **Update code:** `git pull` (a MERGE pull — NOT `--ff-only`; feature-factory
  accepts land merge commits on this host's `main`, so it diverges from origin
  legitimately between pushes).
- **Web-only deploy:** `docker compose -f compose.yml -f compose.prod.yml up -d
  --build --no-deps web`. The `--no-deps` is critical — a plain
  `up -d --build web` also recreates the `worker` container, which KILLS any
  long-running job in the worker (this has interrupted the history backfill
  twice). Only omit `--no-deps` when you intend to rebuild the worker too.
- **Worker deploy:** `... up -d --build worker` — but first confirm no
  long-running script is executing inside it
  (`pgrep -af backfill_snapshots` etc.).
- Health check: `docker compose -f compose.yml -f compose.prod.yml exec -T
  worker uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"`.
- Postgres-gated tests run against the `cockpit_test` DB, not `cockpit`.

## Two Claude tiers on this box — keep them distinct
- **YOU (root remote-control):** trusted operator access — deploys, debugging
  the live system, real ops. Powerful; act with the posture above.
- **The feature factory (`factory` user):** a deliberately SANDBOXED, code-only,
  credential-less builder for untrusted/experimental changes, gated by
  diff-review before accept. Do not confuse its "it's sandboxed so it's safe"
  reputation with your own root access — you have none of that containment.

## Known gotchas / open items (see TODO.md for the live list)
- The history backfill (`scripts/backfill_snapshots.py`) is not yet
  resumable — a worker rebuild mid-run forces a full Robinhood re-pull. Don't
  rebuild the worker while it runs.
- IBKR gateway is intentionally `IB_ENABLED=false` (paper account pending).
- Robinhood session expires ~every 4.7 days; the dashboard button refreshes it.

## Session hygiene
- When context gets long, `/compact` or start a fresh session — a new session
  re-reads this file and orients cleanly.
- Resume a prior session with `claude --resume`.
