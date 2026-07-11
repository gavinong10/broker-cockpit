# TODO

## Blocking / external
- [ ] **Verify IBKR paper trading account is active**, then finish Task 5 + Task 8 of Phase 0.
      IBKR confirmed the paper account application would process "by next business day"
      (submitted 2026-07-10). Credentials are already correct in `.env` on the VPS
      (`gavinong1992paper`) — this was NOT a credentials bug, confirmed via an X11
      screenshot of the actual IBKR auth-failure dialog.
      **Resume steps** (VPS, `/root/broker-cockpit`):
      ```bash
      sed -i 's/^IB_ENABLED=false/IB_ENABLED=true/' .env   # re-enable the worker's reconnect loop
      docker compose -f compose.yml -f compose.prod.yml up -d --build worker
      docker compose -f compose.yml -f compose.prod.yml up -d ib-gateway
      docker compose -f compose.yml -f compose.prod.yml logs -f ib-gateway   # watch for "IBC: Login has completed"
      docker compose -f compose.yml -f compose.prod.yml exec worker \
        uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"
      # expect {'db': 'ok', 'gateway': 'connected'}
      ```
      Then: restart `ib-gateway` once to confirm self-heal + Discord disconnect/reconnect
      embeds, and start the Task 8 seven-day unattended soak (Phase 0 exit gate).
      Phase 1's IBKR sync module (app/ibkr_sync.py, mock-tested) activates
      automatically on the first successful connect — after login completes, also
      verify `broker_accounts` gains an ibkr row and the dashboard shows both brokers.

- [ ] **RH session renewal ~every 4.7 days**: Robinhood clamps token lifetime to
      407891s. When the dashboard banner / Discord ping fires: locally
      `rm secrets/rh-session.pickle && cd apps/worker && uv run python scripts/rh_login.py`,
      then `scp secrets/rh-session.pickle root@204.168.169.27:/root/broker-cockpit/secrets/`.
      Future improvement if the cadence annoys: refresh-token flow in the worker
      (needs a writable pickle mount — currently :ro — and rotation-safe persistence).

## Monday 2026-07-13 runbook — import the AI call-spread basket
1. Place the trades in Robinhood (per session 5a6b9ddd's final plan).
2. Wait ≤15 min for the market-hours sync (or trigger from the Mac:
   `ssh root@204.168.169.27 "cd /root/broker-cockpit && docker compose -f compose.yml -f compose.prod.yml exec -T worker uv run python -c \"import os,httpx;print(httpx.post('http://localhost:8000/internal/sync/robinhood',headers={'X-Internal-Token':os.environ['INTERNAL_API_TOKEN']},timeout=120).json())\""`).
3. Preview: `python3 scripts/import_basket.py 5a6b9ddd-490e-4ae6-91c7-74db07e4140f --dry-run`
   — every executed leg should now match; legs you skipped will show as conflicts (fine:
   edit them out when prompted, or accept partial matching if the endpoint allocated the rest).
4. Real import: same command without --dry-run → basket live at
   https://cockpit.gavinong.org/baskets/<slug>.
5. Sanity: basket card on the dashboard, Exposure tab reflects the new option exposure,
   basket snapshot appears after tonight's 21:10 UTC run.

## Phase 1 status (2026-07-11): LIVE on Robinhood
Deployed and verified on cockpit.gavinong.org with real data: 51 equities + 21
options synced (account 937353795), total $375,540.08, snapshot #1 recorded,
15-min market-hours sync loop running. Remaining: IBKR live activation (above)
and the optional UW greeks enrichment (plan Task 9, needs UW_API_KEY).

## Phase 0 status snapshot (2026-07-11)
Done: repo/compose skeleton, schema v1 + migrations, Google login + roles (live,
verified), internal API auth, GCS backups (live upload + restore drill both passed),
VPS deploy with Caddy TLS (live), SSH hardened (key-only + fail2ban), daily heartbeat
code shipped. See `docs/superpowers/specs/2026-07-10-broker-cockpit-design.md` for the
full design and `docs/superpowers/plans/2026-07-10-phase-0-skeleton.md` for the task
plan. Blocked only on IBKR paper account activation (above).

## Not blocked on IBKR — can build now
- Phase 1 unified portfolio view UI (position list, allocation views, position detail
  page) against the Robinhood mirror, which needs no IBKR connectivity.
- Robinhood position/account sync into the `positions`/`snapshots` tables (RH MCP
  tools are already authenticated in this environment).
- Position detail page + journal thread UI shell (Phase 2), even before order
  placement is wired up.
- `ib_async` position-sync and order-draft logic can be written and unit-tested
  against a mocked `IB` client now; only live integration testing needs the real
  gateway.
