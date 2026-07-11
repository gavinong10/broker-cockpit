# TODO

## Blocking / external
- [ ] **Verify IBKR paper trading account is active**, then finish Task 5 + Task 8 of Phase 0.
      IBKR confirmed the paper account application would process "by next business day"
      (submitted 2026-07-10). Credentials are already correct in `.env` on the VPS
      (`gavinong1992paper`) — this was NOT a credentials bug, confirmed via an X11
      screenshot of the actual IBKR auth-failure dialog.
      **Resume steps** (VPS, `/root/broker-cockpit`):
      ```bash
      docker compose -f compose.yml -f compose.prod.yml up -d ib-gateway
      docker compose -f compose.yml -f compose.prod.yml logs -f ib-gateway   # watch for "IBC: Login has completed"
      docker compose -f compose.yml -f compose.prod.yml exec worker \
        uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"
      # expect {'db': 'ok', 'gateway': 'connected'}
      ```
      Then: restart `ib-gateway` once to confirm self-heal + Discord disconnect/reconnect
      embeds, and start the Task 8 seven-day unattended soak (Phase 0 exit gate).

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
