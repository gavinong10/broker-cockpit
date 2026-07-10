# RESTORE.md — Cold restore & drill runbook

Backups are nightly `pg_dump` gzips pushed to Backblaze B2 by the `backup`
compose service (cron: 08:15 UTC, see `infra/backup/crontab`), named
`cockpit-<UTC-stamp>.sql.gz` in bucket `broker-cockpit-backups`, with 30-day
retention enforced by the same job.

> Commands below use `docker compose` (the plugin bundled with fresh Docker
> installs). On hosts that only have the standalone binary (e.g. the local
> colima dev Mac), substitute `docker-compose`. `scripts/restore-drill.sh`
> auto-detects whichever is present.

## Cold restore (new VPS, total loss of old host)

Assumes: your password manager holds every value in `.env.example`
(POSTGRES_*, AUTH_*, INTERNAL_API_TOKEN, IB_USER/IB_PASSWORD, DISCORD_WEBHOOK_URL,
B2_KEY_ID, B2_APP_KEY, B2_BUCKET) and you have push access to the repo remote.

1. **Provision** a fresh Ubuntu 24.04 VPS. Point the DNS A record
   (`cockpit.<your-domain>`) at its IP. `ufw allow 22 80 443` only.

2. **Install Docker** (includes the compose plugin):

   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

3. **Clone the repo**:

   ```bash
   git clone <repo-remote-url> broker-cockpit && cd broker-cockpit
   ```

4. **Recreate `.env`** from the password manager:

   ```bash
   cp .env.example .env
   # fill every variable with the values stored in the password manager
   ```

   Keep `IB_TRADING_MODE=paper` unless you are deliberately doing the live swap.

5. **Start Postgres only** (empty data volume) and wait for healthy:

   ```bash
   docker compose up -d postgres
   docker compose ps postgres   # wait for (healthy)
   ```

6. **Pull the latest dump from B2** using the backup image (rclone lives there;
   nothing to install on the host):

   ```bash
   set -a; . ./.env; set +a
   docker compose build backup
   LATEST=$(docker compose run --rm backup rclone lsf ":b2:${B2_BUCKET}/" \
     --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY" | sort | tail -1)
   docker compose run --rm backup sh -c \
     "rclone cat ':b2:${B2_BUCKET}/${LATEST}' --b2-account $B2_KEY_ID --b2-key $B2_APP_KEY" \
     > /tmp/restore.sql.gz
   ```

7. **Restore with psql** into the primary database (`$POSTGRES_DB`, i.e. `cockpit`):

   ```bash
   gunzip -c /tmp/restore.sql.gz | docker compose exec -T postgres \
     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
   ```

   Sanity check:

   ```bash
   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -tAc "SELECT count(*) FROM users; SELECT count(*) FROM audit_log;"
   ```

8. **Bring up the full stack** (prod uses the caddy overlay):

   ```bash
   docker compose -f compose.yml -f compose.prod.yml up -d --build
   ```

   The `worker` runs `alembic upgrade head` on boot — a dump from an older
   schema is migrated forward automatically. Verify:
   `docker compose exec worker uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"`
   → `{'db': 'ok', 'gateway': 'connected'}`, and Google login works at
   `https://cockpit.<your-domain>`.

9. **Confirm the nightly backup resumes**: next morning after 08:15 UTC, check
   the B2 bucket for a fresh `cockpit-*.sql.gz`, or run one immediately:

   ```bash
   docker compose run --rm backup sh -c 'PGPASSWORD=$POSTGRES_PASSWORD backup.sh'
   ```

## Drill cadence

Run a restore drill **quarterly** (and after any change to the backup service,
Postgres major version, or schema tooling). The automated form is
`scripts/restore-drill.sh`: it pulls the latest B2 dump, restores it into a
scratch database `drill`, and compares `users` row counts against the live
`cockpit` database — expect `RESTORE DRILL PASS (users: N)`.

```bash
set -a; . ./.env; set +a
sh scripts/restore-drill.sh
```

A failed drill is an incident: the backups are not proven restorable. Diagnose
before the next nightly cycle overwrites your window.
