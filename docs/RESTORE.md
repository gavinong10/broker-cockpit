# RESTORE.md — Cold restore & drill runbook

Rebuild the broker-cockpit prod host from nothing. The restore order is:

1. **Provision the host** — `scripts/provision_host.sh` (Docker, ufw, fail2ban,
   sshd hardening, the `edge` network, the remote-control unit, feature-factory).
2. **Restore secrets** — `scripts/restore_secrets.sh` (the `.env` + `secrets/`
   set, from the encrypted GCS blob).
3. **Restore the database** — from the nightly GCS `pg_dump`.
4. **Bring up the stack** — `docker compose ... up -d`.
5. **External/manual checklist** — the things no script can recreate (DNS,
   OAuth, GCP, GitHub deploy keys, IBKR, claude.ai logins).

There are **two independent GCS backup streams** in bucket `$GCS_BUCKET`
(`broker-cockpit-backups-gavinong`, project `broker-cockpit-8496`):

| Stream | Object | Cadence | Encrypted? | Made by |
| --- | --- | --- | --- | --- |
| Database | `cockpit-<stamp>.sql.gz` (bucket root) | nightly 08:15 UTC, 30-day retention | no (bucket-private) | `backup` compose service |
| Secrets | `secrets/cockpit-secrets-<stamp>.tar.gz.gpg` | **manual** (after any secret change), keep last 10 | **yes — gpg AES256** | `scripts/backup_secrets.sh` |

The service account is
`cockpit-backup@broker-cockpit-8496.iam.gserviceaccount.com`, scoped via
bucket-level IAM to `objectAdmin` on only that one bucket. Both streams use the
same rclone remote string
(`:gcs,service_account_file=$GCS_KEY_FILE,bucket_policy_only=true:$GCS_BUCKET`),
and both run rclone **inside the `backup` container** — the host has no rclone.

> Commands below use `docker compose` (the plugin bundled with fresh Docker
> installs; the VPS is on v2). On the local colima dev Mac only the standalone
> `docker-compose` binary works. `restore-drill.sh`, `backup_secrets.sh`,
> `restore_secrets.sh`, and `provision_host.sh` all auto-detect whichever is
> present.

---

## The chicken-and-egg problem

The DB restore and the secrets backup/restore all rely on the `backup`
container's rclone, which needs `secrets/gcs-backup-key.json` to authenticate.
So the **very first** secret you must recover is the GCS key — and it is inside
the encrypted secrets blob you are trying to download. Break the loop with
whichever you have:

- **You still have gcloud/console access to `broker-cockpit-8496`:** mint a
  fresh key directly (no old secret needed):

  ```bash
  mkdir -p secrets
  gcloud iam service-accounts keys create secrets/gcs-backup-key.json \
    --iam-account=cockpit-backup@broker-cockpit-8496.iam.gserviceaccount.com
  chmod 600 secrets/gcs-backup-key.json
  ```

  With that one file in place, `restore_secrets.sh` can pull the rest.

- **You kept a copy of `gcs-backup-key.json` in your password manager:** drop it
  at `secrets/gcs-backup-key.json` (`chmod 600`) and proceed.

Everything else (DB dump, the full secrets set) flows from there.

---

## 1. Provision the host

Fresh Ubuntu 24.04 box, root SSH. Get the repo, then run the provisioner:

```bash
git clone <repo-remote-url> broker-cockpit && cd broker-cockpit
scripts/provision_host.sh --check   # report what it WOULD do (changes nothing)
scripts/provision_host.sh           # apply
```

`provision_host.sh` is idempotent and safe to re-run (on the live box it is a
verified no-op). It ensures: apt basics, Docker + compose plugin
(get.docker.com), ufw allowing **only** 22/80/443 (22 first, then enable — never
locks you out), fail2ban + the sshd jail, the sshd hardening drop-in
(`PasswordAuthentication no`), the external `edge` docker network (used by
`compose.prod.yml` to reach the beta-platform stack), the
`claude-remote-control.service` systemd unit (from
`docs/host/claude-remote-control.service`), the feature-factory host setup (via
`scripts/setup_feature_factory.sh`), and `/root/.claude/CLAUDE.md` (from
`docs/host/root-claude-md.md`).

It deliberately does **NOT** overwrite an existing `.env` or `secrets/`,
regenerate SSH keys, restart running app containers, or touch the database —
those are the next steps.

> ufw is only a backstop: Docker publishes container ports via iptables rules
> that run ahead of ufw, so a `ports:` entry in compose is *not* protected by
> ufw. `compose.prod.yml` resets every published port except caddy's 80/443 for
> exactly this reason.

## 2. Restore secrets (`.env` + `secrets/`)

You need `secrets/gcs-backup-key.json` in place first (see the chicken-and-egg
section). Then:

```bash
# provision_host.sh left a placeholder-free host but no .env yet; you still need
# a minimal .env for GCS_BUCKET/GCS_KEY_FILE so restore_secrets.sh can build the
# rclone remote and the backup image can start. Simplest: restore the whole set.
scripts/restore_secrets.sh            # latest encrypted blob -> ./secrets-restore-<stamp>/
```

It downloads the latest `secrets/cockpit-secrets-*.tar.gz.gpg`, prompts for the
**passphrase you stored in your password manager**, decrypts (gpg AES256), and
extracts into a **staging dir** — it does NOT auto-overwrite live files. Review,
then copy into place and fix perms (the script prints the exact commands):

```bash
STAGE=./secrets-restore-<stamp>
cp "$STAGE/.env"          ./.env
cp -a "$STAGE/secrets/."  ./secrets/
chown root:root ./.env && chmod 600 ./.env
chmod 700 ./secrets && chmod 600 ./secrets/*
# extras if present in the blob: root/.ssh/id_ed25519*, root/.claude/.credentials.json,
#   etc/feature-factory.env — copy to /root/.ssh, /root/.claude, /etc respectively
rm -rf "$STAGE"           # once satisfied
```

> **Bootstrap note:** if you had to mint a fresh GCS key in step 0, the one now
> inside `secrets/` from the blob is the OLD (possibly revoked) key — keep the
> freshly-minted one you just created and re-run `backup_secrets.sh` afterward
> so the blob reflects reality.

Keep `IB_TRADING_MODE=paper` unless you are deliberately doing the live swap
(see DEPLOY.md).

## 3. Restore the database

Bring up Postgres on an empty volume, pull the latest dump, load it:

```bash
set -a; . ./.env; set +a
docker compose up -d postgres
docker compose ps postgres          # wait for (healthy)

docker compose build backup
REMOTE=":gcs,service_account_file=${GCS_KEY_FILE},bucket_policy_only=true:${GCS_BUCKET}"
LATEST=$(docker compose run --rm --no-deps -T backup rclone lsf "${REMOTE}/" </dev/null | grep 'sql.gz' | sort | tail -1)
docker compose run --rm --no-deps -T backup rclone cat "${REMOTE}/${LATEST}" </dev/null > /tmp/restore.sql.gz

gunzip -c /tmp/restore.sql.gz | docker compose exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

# sanity
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -tAc "SELECT count(*) FROM users; SELECT count(*) FROM audit_log;"
```

## 4. Bring up the full stack (prod overlay)

```bash
docker compose -f compose.yml -f compose.prod.yml up -d --build
```

The overlay: removes web's & postgres's published host ports, adds caddy on
80/443 (custom build = stock caddy + the Cloudflare-DNS plugin, needed for the
`*.beta` wildcard cert; cockpit's own cert stays HTTP-01), and joins the `edge`
network. The `worker` runs `alembic upgrade head` on boot, so an older-schema
dump is migrated forward automatically. Verify:

```bash
docker compose -f compose.yml -f compose.prod.yml exec -T worker \
  uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"
```

Expect `{'db': 'ok', 'gateway': ...}` — `gateway` is `disabled` while IBKR is
off (`IB_ENABLED=false`, paper pending), `connected` once the gateway is up.
Then confirm Google login works at `https://cockpit.<your-domain>`.

## 5. Re-enable the remote-control + feature-factory

`provision_host.sh` installed and enabled `claude-remote-control.service`, but it
needs a claude.ai login to actually connect (see the manual checklist). After
logging in:

```bash
systemctl restart claude-remote-control
systemctl status claude-remote-control --no-pager
```

The feature-factory host setup ran in step 1; if the builder credential is not
yet present it will have prompted (subscription: `claude login` as the `factory`
user, or api-key mode). Re-run `scripts/setup_feature_factory.sh` to complete it
and re-verify the isolation self-check.

---

## External / manual recovery checklist

None of these can be scripted from the box — they live in external consoles and
need your logins. Recreate them by hand:

- [ ] **DNS (Cloudflare):** A record `cockpit.<your-domain>` → new VPS IP,
      **grey-cloud / DNS-only** (Cloudflare proxy OFF, or Caddy's Let's Encrypt
      HTTP-01 breaks). If using the beta stack, the `*.beta` records + the
      Cloudflare **API token** (DNS-edit scope) that caddy uses for DNS-01 —
      the token lives in `.env` as `CLOUDFLARE_API_TOKEN` (restored in step 2).
- [ ] **Google OAuth (console.cloud.google.com → `broker-cockpit-8496`):** the
      OAuth 2.0 client's **Authorized redirect URI** must include
      `https://cockpit.<your-domain>/api/auth/callback/google`; the client
      ID/secret are in `.env`; re-add any **OAuth test users** (the allow-listed
      Google accounts) if the app is in testing mode.
- [ ] **GCP project / billing / bucket / service account:** if the whole project
      is lost, recreate project `broker-cockpit-8496`, link billing, create the
      `broker-cockpit-backups-gavinong` bucket (uniform bucket-level access),
      the `cockpit-backup` service account scoped to `objectAdmin` on only that
      bucket, and a fresh JSON key → `secrets/gcs-backup-key.json`.
- [ ] **GitHub deploy keys:** register the restored `/root/.ssh/id_ed25519.pub`
      as a **read-only deploy key** on `gavinong10/broker-cockpit` (for `git
      pull` on the VPS), and the feature-factory write key
      (`secrets/feature_runner_key.pub`, via the `github-factory` SSH host alias)
      as a **read/write deploy key** for feature-factory pushes. If the keys
      themselves are lost, `provision_host.sh` / `setup_feature_factory.sh`
      generate fresh ones — then register the new public keys.
- [ ] **IBKR account:** the paper (and later live headless secondary) account
      credentials go in `.env` (`IB_USER`/`IB_PASSWORD`/`IB_TRADING_MODE`);
      account setup itself is manual at interactivebrokers.com.
- [ ] **claude.ai logins (two, keep distinct):** (a) root **remote-control** —
      log in so `claude-remote-control.service` can connect; (b) the
      **factory** user's builder credential (subscription `claude login` as
      `factory`, or an api-key with a console spend limit). See
      `docs/host/root-claude-md.md` for the two-tier model.

---

## Making a fresh secrets backup

After any secret changes (Robinhood session rotation, DB password, new GCS key,
OAuth secret, deploy keys), run — **manually, as root, from the repo root**:

```bash
scripts/backup_secrets.sh
```

It tars `.env` + `secrets/` (plus `/etc/feature-factory.env`,
`/root/.ssh/id_ed25519*`, `/root/.claude/.credentials.json` when present),
gpg-encrypts with a passphrase you type (AES256; the passphrase is never written
to disk or argv), uploads to `gs://$GCS_BUCKET/secrets/`, and prunes to the last
10. **The passphrase lives only in your password manager** — without it the
backup is unrecoverable, which is the whole point (it is why this step is
manual and never cron'd: automating it would require storing the passphrase on
the box beside the secrets it protects).

---

## Deploy / update rules (apply during restore too)

- **`git pull` is a MERGE pull, NOT `--ff-only`.** The feature-factory lands
  accept merges directly on the VPS's `main`, so its history is legitimately
  ahead of origin between pushes; `--ff-only` will refuse.
- **`--no-deps` on targeted rebuilds.** `docker compose ... up -d --build
  --no-deps web` — a plain `up -d --build web` also recreates `worker`, killing
  any long-running job (the history backfill is not yet resumable). Only omit
  `--no-deps` when you intend to rebuild the worker too, and first check nothing
  long-running is executing inside it.

---

## Drill cadence

Run a restore drill **quarterly** (and after any change to the backup service,
Postgres major version, or schema tooling):

```bash
set -a; . ./.env; set +a
sh scripts/restore-drill.sh
```

It pulls the latest DB dump, restores into a scratch `drill` database, and
compares `users` row counts against live — expect `RESTORE DRILL PASS
(users: N)`. A failed drill is an incident: the backups are not proven
restorable. Diagnose before the next nightly cycle overwrites your window.

For the **secrets** stream, the equivalent drill is: `scripts/restore_secrets.sh`
into a staging dir and confirm the `.env` there matches live
(`diff secrets-restore-*/.env ./.env`) — do this whenever you rotate the backup
passphrase.
