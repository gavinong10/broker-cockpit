# RESTORE.md — Cold restore & drill runbook

Backups are nightly `pg_dump` gzips pushed to Google Cloud Storage by the
`backup` compose service (cron: 08:15 UTC, see `infra/backup/crontab`),
named `cockpit-<UTC-stamp>.sql.gz` in bucket `$GCS_BUCKET`
(`broker-cockpit-backups-gavinong`, project `broker-cockpit-8496`), with
30-day retention enforced by the same job.

> Commands below use `docker compose` (the plugin bundled with fresh Docker
> installs). On hosts that only have the standalone binary (e.g. the local
> colima dev Mac), substitute `docker-compose`. `scripts/restore-drill.sh`
> auto-detects whichever is present.

## Cold restore (new VPS, total loss of old host)

Assumes: your password manager holds every value in `.env.example`
(POSTGRES_*, AUTH_*, INTERNAL_API_TOKEN, IB_USER/IB_PASSWORD,
DISCORD_WEBHOOK_URL, GCS_BUCKET) plus a **separate copy of the GCS service
account key** (`secrets/gcs-backup-key.json` — a JSON key file, not an env
var; keep it in the password manager as a file attachment or a secure note),
and you have push/pull access to the repo remote.

The service account is `cockpit-backup@broker-cockpit-8496.iam.gserviceaccount.com`,
scoped via bucket-level IAM to `objectAdmin` on only the one backup bucket —
nothing else in the GCP project. To mint a fresh key if the old one is lost:

```bash
gcloud iam service-accounts keys create secrets/gcs-backup-key.json \
  --iam-account=cockpit-backup@broker-cockpit-8496.iam.gserviceaccount.com
```

1. **Provision** a fresh Ubuntu 24.04 VPS. Point the DNS A record
   (`cockpit.<your-domain>`) at its IP. `ufw allow 22 80 443` only — and be
   aware Docker publishes container ports via iptables rules that run ahead
   of ufw, so any `ports:` entry in compose is *not* protected by ufw alone;
   `compose.prod.yml` resets every port except caddy's 80/443 for this reason.

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

5. **Restore the GCS key file**: place the service account key at
   `secrets/gcs-backup-key.json` (`chmod 600`) — from the password manager,
   or minted fresh with the `gcloud iam service-accounts keys create` command
   above if you still have console/gcloud access to the project.

6. **Start Postgres only** (empty data volume) and wait for healthy:

   ```bash
   docker compose up -d postgres
   docker compose ps postgres   # wait for (healthy)
   ```

7. **Pull the latest dump from GCS** using the backup image (rclone lives
   there; nothing to install on the host):

   ```bash
   set -a; . ./.env; set +a
   docker compose build backup
   REMOTE=":gcs,service_account_file=${GCS_KEY_FILE}:${GCS_BUCKET}"
   LATEST=$(docker compose run --rm backup rclone lsf "${REMOTE}/" | sort | tail -1)
   docker compose run --rm backup rclone cat "${REMOTE}/${LATEST}" > /tmp/restore.sql.gz
   ```

8. **Restore with psql** into the primary database (`$POSTGRES_DB`, i.e. `cockpit`):

   ```bash
   gunzip -c /tmp/restore.sql.gz | docker compose exec -T postgres \
     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
   ```

   Sanity check:

   ```bash
   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -tAc "SELECT count(*) FROM users; SELECT count(*) FROM audit_log;"
   ```

9. **Bring up the full stack** (prod uses the caddy overlay):

   ```bash
   docker compose -f compose.yml -f compose.prod.yml up -d --build
   ```

   The `worker` runs `alembic upgrade head` on boot — a dump from an older
   schema is migrated forward automatically. Verify:
   `docker compose exec worker uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"`
   → `{'db': 'ok', 'gateway': 'connected'}`, and Google login works at
   `https://cockpit.<your-domain>`.

10. **Confirm the nightly backup resumes**: next morning after 08:15 UTC,
    check the GCS bucket for a fresh `cockpit-*.sql.gz`, or run one
    immediately:

    ```bash
    docker compose run --rm backup sh -c 'PGPASSWORD=$POSTGRES_PASSWORD backup.sh'
    ```

## Drill cadence

Run a restore drill **quarterly** (and after any change to the backup service,
Postgres major version, or schema tooling). The automated form is
`scripts/restore-drill.sh`: it pulls the latest GCS dump, restores it into a
scratch database `drill`, and compares `users` row counts against the live
`cockpit` database — expect `RESTORE DRILL PASS (users: N)`.

```bash
set -a; . ./.env; set +a
sh scripts/restore-drill.sh
```

A failed drill is an incident: the backups are not proven restorable. Diagnose
before the next nightly cycle overwrites your window.
