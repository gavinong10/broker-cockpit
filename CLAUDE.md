# broker-cockpit

**Check `TODO.md` first, every session** — it tracks imminent/blocking to-dos (currently:
verifying the IBKR paper account activated) and a snapshot of what's done vs pending.

Full design: `docs/superpowers/specs/2026-07-10-broker-cockpit-design.md`.
Phase 0 task plan: `docs/superpowers/plans/2026-07-10-phase-0-skeleton.md`.

## Operational facts (don't re-derive these)

- **Prod VPS:** `204.168.169.27`, root SSH (key-only, password auth disabled), repo at
  `/root/broker-cockpit`. Domain: `https://cockpit.gavinong.org` (Cloudflare DNS,
  grey-clouded / DNS-only — must stay off Cloudflare's proxy or Caddy's Let's Encrypt
  breaks).
- **Local dev:** this Mac runs Docker via **colima**, and only the standalone
  `docker-compose` binary works here — NOT the `docker compose` plugin. The VPS has
  the real plugin, so VPS commands use `docker compose` (space); local dev commands
  use `docker-compose` (hyphen). Don't mix them up.
- **GitHub:** `git@github.com:gavinong10/broker-cockpit.git`, private repo. VPS clones
  via a read-only deploy key at `/root/.ssh/id_ed25519` on the VPS (not your laptop key).
- **GCP project:** `broker-cockpit-8496` (billing linked, OAuth client + GCS backups
  live here). Backup bucket `broker-cockpit-backups-gavinong`, service account
  `cockpit-backup@broker-cockpit-8496.iam.gserviceaccount.com` scoped to only that
  bucket. Key file: `secrets/gcs-backup-key.json` (gitignored, mounted into the
  `backup` container).
- **Secrets:** `.env` is gitignored; `.env.vps` in this repo is a local, gitignored
  working copy of the real VPS `.env` (has real generated secrets — never commit it,
  covered by `.env.*` in `.gitignore`). To push changes: edit `.env.vps` locally, then
  `scp` it to `/root/broker-cockpit/.env` on the VPS and restart the affected
  containers.
- **IBKR:** paper trading only right now (`IB_TRADING_MODE=paper`). The live headless
  secondary-user swap (trading-permissions-only, SLS opt-out) is a deliberate future
  step, not yet done — do not conflate paper and live credentials.

- **Postgres-gated tests:** run against the isolated `cockpit_test` database
  (same local colima postgres, migrated to head via alembic), NOT the dev
  `cockpit` DB — demo-seed rows there FK-block test cleanup. Example:
  `TEST_DATABASE_URL=postgresql+psycopg://cockpit:<pw>@localhost:5432/cockpit_test uv run pytest`.

## Capabilities

- conversation-import — turn a Claude session ID into a live basket (see docs/capabilities/conversation-import.md).

## Standing rules for this project

- Every actual paid/authenticated API call (GCS uploads, Discord posts, IBKR gateway
  login, gcloud resource creation) needs explicit per-action user OK before running it
  — not just once at project start.
- Non-protective trading actions are propose-and-confirm by default; only pre-approved
  protective rules (stop-losses, trailing stops) may fire autonomously. See the design
  spec §3 before changing this.
- Prefer editing `docs/superpowers/plans/*.md` task-by-task with review gates between
  tasks (subagent-driven-development style) over large unreviewed batches of changes,
  especially for anything touching auth, money, or broker connectivity.
