# Phase 1 — Unified Portfolio View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement task-by-task.
> Read `CLAUDE.md` and `TODO.md` at the repo root before starting any task.

**Goal:** Real positions from Robinhood (mirror) flowing into Postgres on a schedule, an RH-feel dashboard at cockpit.gavinong.org showing aggregated positions / day P&L / allocations, a position detail page, daily portfolio snapshots feeding a value chart, and an IBKR sync module fully written and mock-tested so it activates the moment the paper gateway logs in.

**Architecture:** Worker owns all broker I/O: a `robin_stocks`-based RH client (pickle session file, no creds in env) and an `ib_async`-based IBKR position subscriber (gated on `gateway.connected`), both normalizing into the same `instruments`/`positions` tables. Web reads only via `workerFetch` internal API. Masking for `viewer` role applied in the web layer.

**Tech Stack:** existing stack + `robin_stocks` (worker). No new services.

## Global Constraints (from spec + standing rules)

- RH is **read-only mirror** — never call any RH order endpoint.
- Broker = source of truth for money; DB positions are a cache, rebuilt idempotently on every sync.
- USD-native display everywhere.
- `viewer` role: server-side read-only; `mask_amounts=true` viewers see percent weights, never dollars.
- Every live authenticated call during execution (RH login/sync, UW) needs explicit per-action user OK.
- Local dev uses `docker-compose` (standalone, colima); VPS uses `docker compose` (plugin). See CLAUDE.md.
- UI charts: the executing agent MUST load the `dataviz` skill before writing any chart code.

---

## Task 0 — Human prerequisites (user-only)

- [ ] **RH session bootstrap**: run the interactive login helper (built in Task 1) once on the Mac — it performs robin_stocks login with MFA and writes `secrets/rh-session.pickle`; then `scp` it to `/root/broker-cockpit/secrets/` on the VPS. Session lasts ~30 days; the dashboard will nag when it goes stale.
- [ ] (Optional, Task 9) **UW API key** in `.env` as `UW_API_KEY=` for options greeks/IV enrichment. Skippable — UI degrades gracefully.

---

## Task 1 — RH client: login helper, fetch, normalize, upsert

**Files:**
- Create: `apps/worker/app/robinhood.py`, `apps/worker/scripts/rh_login.py`, `apps/worker/tests/test_rh_normalize.py`, `apps/worker/tests/fixtures/rh_stock_positions.json`, `apps/worker/tests/fixtures/rh_option_positions.json`
- Modify: `apps/worker/pyproject.toml` (add `robin_stocks>=3.4`), `compose.yml` (mount `./secrets:/secrets:ro` into worker), `.env.example` (`RH_SESSION_FILE=/secrets/rh-session.pickle`)
- Produces: `sync_robinhood(engine) -> SyncResult{positions: int, account: str}` — idempotent full-mirror upsert.

**Interfaces (consumed by Tasks 2/4):**
```python
@dataclass
class SyncResult:
    account_external_id: str
    equity_positions: int
    option_positions: int
    cash_usd: Decimal
```

**Steps:**
- [ ] Add `robin_stocks>=3.4` to pyproject; `uv sync`.
- [ ] Write `apps/worker/scripts/rh_login.py`: interactive CLI (`uv run python scripts/rh_login.py`) that calls `robin_stocks.robinhood.login(username, password, mfa_code, pickle_path=...)` prompting via `getpass`, writes the pickle to `../../secrets/rh-session.pickle`, then calls `load_account_profile()` and prints the account number as proof. Never reads creds from env or argv.
- [ ] Record realistic fixture JSONs (shapes from robin_stocks docs: `get_open_stock_positions`, `get_open_option_positions`, `load_account_profile`, plus quote/instrument lookups) — include one equity with fractional shares, one short option (negative qty via `type: short`), and non-trivial average_buy_price.
- [ ] Write failing tests `test_rh_normalize.py`:
  - `test_normalize_equities`: fixture → list of `(symbol, sec_type='STK', qty, avg_cost_usd, last_price_usd)` with fractional qty preserved as Decimal.
  - `test_normalize_options`: OCC symbol built as `{SYM}{YYMMDD}{C/P}{strike*1000:08d}`, short positions negative qty, multiplier 100, expiry/strike/right populated.
  - `test_upsert_idempotent`: run upsert twice against a Postgres test DB (skipif no TEST_DATABASE_URL) → same row counts; a position missing from the second payload is deleted (full-mirror semantics).
- [ ] Verify failure: `uv run pytest tests/test_rh_normalize.py` → import error.
- [ ] Implement `apps/worker/app/robinhood.py`:
  - `rh_session()` — logs in from `settings.rh_session_file` pickle only (`login(pickle_path=..., store_session=True)` with no creds); raises `RHAuthError` if the session is invalid/expired.
  - `fetch_raw()` — account profile, stock positions (with instrument→symbol resolution and latest quotes), option positions (with option instrument details and marks).
  - `normalize(raw) -> (account, [PositionRow])` — pure function, unit-testable, all Decimals, all USD.
  - `upsert(engine, account, rows)` — single transaction: upsert `broker_accounts` (broker='robinhood', cash_usd, last_synced_at=now), upsert instruments by (symbol, sec_type, occ fields), upsert positions keyed (broker_account_id, instrument_id) with qty/avg_cost_usd/last_price_usd/prev_close_usd/updated_at, delete vanished positions.
  - `sync_robinhood(engine) -> SyncResult` — composes the above; audit row `sync.robinhood.ok` with counts, or `sync.robinhood.error` and re-raise.
- [ ] Migration `0002`: add to `broker_accounts`: `cash_usd Numeric(18,2) default 0`, `last_synced_at timestamptz null`; add to `positions`: `last_price_usd Numeric(18,4) null`, `prev_close_usd Numeric(18,4) null`. `uv run alembic revision --autogenerate -m "rh sync columns"` and review.
- [ ] Verify pass: full suite green locally (`uv run pytest -q`).
- [ ] Commit: `Task 1: robinhood client - login helper, normalize, idempotent mirror upsert`.

---

## Task 2 — Sync scheduler + staleness + manual trigger

**Files:**
- Create: `apps/worker/app/scheduler.py`, `apps/worker/tests/test_scheduler.py`
- Modify: `apps/worker/app/main.py`
- Produces: RH sync every 15 min during US market hours (13:30–20:00 UTC, Mon–Fri; hourly off-hours), `POST /internal/sync/robinhood`, staleness surfaced via API.

**Steps:**
- [ ] Failing tests for pure helpers: `is_market_hours(dt)` (UTC-based, weekends false) and `next_sync_delay(dt) -> 900 | 3600`.
- [ ] Implement `scheduler.py`: `sync_loop(engine)` — sleep per `next_sync_delay`, call `sync_robinhood`, guard every iteration; on `RHAuthError` → Discord alert "Robinhood session expired — re-run rh_login + scp" (once per 6h, not every loop) + audit `sync.robinhood.auth_expired`; on 3 consecutive other failures → Discord alert.
- [ ] Wire startup task in `main.py`; add `POST /internal/sync/robinhood` (internal-auth) that runs one sync and returns the SyncResult as JSON.
- [ ] Verify: tests green; local compose up → `docker-compose exec worker uv run python -c "import httpx; print(httpx.post('http://localhost:8000/internal/sync/robinhood', headers={'X-Internal-Token':'<token>'}).json())"` → either real counts (if session file present) or a clean 502-style JSON error `{"error": "rh_auth"}` — NOT a traceback.
- [ ] Commit: `Task 2: RH sync scheduler, staleness alerts, manual trigger endpoint`.

---

## Task 3 — Daily snapshots job

**Files:**
- Create: `apps/worker/app/snapshots.py`, `apps/worker/tests/test_snapshots.py`
- Modify: `apps/worker/app/main.py`
- Produces: one `snapshots` row per UTC day at 21:10 UTC (after US close): `total_value_usd = Σ(qty·last_price·multiplier) + Σcash`, `per_account` jsonb breakdown. Idempotent upsert on `taken_on`.

**Steps:**
- [ ] Failing test: seed positions/accounts fixtures → `compute_snapshot(engine)` returns expected totals (include an option: qty·mark·100); running `record_snapshot` twice for one day → 1 row.
- [ ] Implement + wire a startup asyncio task reusing `seconds_until_next(21, ...)` pattern from `app/heartbeat.py` (extract shared helper — do not duplicate) with minute offset support, guarded per-iteration.
- [ ] Add `POST /internal/snapshots/run` (internal-auth) for manual/backfill trigger.
- [ ] Verify: tests green; manual trigger writes a row (visible via psql).
- [ ] Commit: `Task 3: daily portfolio snapshots`.

---

## Task 4 — Portfolio internal API

**Files:**
- Create: `apps/worker/app/portfolio_api.py`, `apps/worker/tests/test_portfolio_api.py`
- Modify: `apps/worker/app/main.py` (include router)
- Produces (all internal-auth, all JSON, all Decimals serialized as strings):
  - `GET /internal/portfolio` → `{total_value_usd, day_change_usd, day_change_pct, cash_usd, accounts: [{broker, external_id, last_synced_at, stale}], positions: [{symbol, sec_type, qty, avg_cost_usd, last_price_usd, prev_close_usd, market_value_usd, unrealized_pl_usd, day_change_usd, weight_pct, expiry, strike, right, brokers: [{broker, qty}]}]}` — aggregated across brokers per instrument, with per-broker breakdown.
  - `GET /internal/positions/{symbol}` → detail incl. per-account rows.
  - `GET /internal/snapshots?days=N` → `[{taken_on, total_value_usd}]` ascending.
- `stale` = `last_synced_at` older than 45 min during market hours / 2h off-hours.

**Steps:**
- [ ] Failing tests via TestClient + seeded test DB: aggregation math (two accounts holding same symbol sum correctly), day-change uses prev_close, weight_pct sums to ~100, staleness logic, 404 on unknown symbol, 401 without token.
- [ ] Implement router with plain SQL (SQLAlchemy Core selects — no ORM query layer needed).
- [ ] Verify: suite green.
- [ ] Commit: `Task 4: portfolio internal API (portfolio, position detail, snapshots)`.

---

## Task 5 — Dashboard UI

**Files:**
- Create: `apps/web/src/lib/format.ts`, `apps/web/src/lib/format.test.ts`, `apps/web/src/components/PositionTable.tsx`, `apps/web/src/components/AllocationBar.tsx`, `apps/web/src/components/PortfolioHeader.tsx`
- Modify: `apps/web/src/app/page.tsx`
- Produces: the RH-feel home dashboard, role-aware.

**Steps:**
- [ ] Failing vitest for `format.ts`: `usd()` (grouping, negatives), `pct()`, and `display(value, masked)` → masked mode returns `"•••"` for dollar values but real percents.
- [ ] Implement `format.ts`; tests green.
- [ ] **Load the `dataviz` skill before any chart/allocation-bar code.**
- [ ] Build the page as server components: `PortfolioHeader` (total value, day change ±color), `AllocationBar` (horizontal weight bars by symbol — no pie), `PositionTable` (symbol, qty, price, day change, market value, unrealized P/L; option rows show `AAPL $150 C 12/19` style labels; expandable per-broker breakdown; sorted by market value desc). Data via `workerFetch('/internal/portfolio')`, `cache: 'no-store'`.
- [ ] Role handling: session (`auth()`) → if `mask_amounts`, pass `masked` through so all dollar cells render via `display(..., true)`; viewers see the same page otherwise. Stale accounts render an amber "data stale — last sync X min ago" banner; RH auth-expired renders the re-auth nudge.
- [ ] Verify: `npm run build` clean; local compose up with seeded fixture data (insert via psql script `apps/worker/scripts/seed_demo.py` — part of this task, gitignored? No: commit it, it's harmless demo data guarded to refuse when broker rows already exist) → page renders positions; screenshot-check.
- [ ] Commit: `Task 5: dashboard UI - header, allocation bars, position table, masking`.

---

## Task 6 — Position detail page + portfolio value chart

**Files:**
- Create: `apps/web/src/app/positions/[symbol]/page.tsx`, `apps/web/src/components/ValueChart.tsx`
- Modify: `apps/web/src/app/page.tsx` (link rows; embed chart above table)
- Produces: `/positions/AAPL` detail (aggregate + per-broker lots + placeholder "Journal — coming in Phase 2" section) and an SVG line chart of `snapshots` (server-rendered, no chart lib; **dataviz skill required before writing it**; handles 1-point and empty cases with an honest "history accumulates from go-live" note).

**Steps:**
- [ ] Implement chart + page; masking rules identical to Task 5.
- [ ] Verify: build clean; with seeded snapshots (3 fake days via seed script) the chart renders a line; `/positions/UNKNOWN` → 404 page.
- [ ] Commit: `Task 6: position detail page + snapshot value chart`.

---

## Task 7 — IBKR position sync (mock-tested now, live later)

**Files:**
- Create: `apps/worker/app/ibkr_sync.py`, `apps/worker/tests/test_ibkr_sync.py`
- Modify: `apps/worker/app/ibkr.py` (post-connect hook), `apps/worker/app/main.py`
- Produces: on every gateway connect + every 15 min while connected: `ib.portfolio()` items normalized into the same tables under broker='ibkr' (STK + OPT conIds, avg cost, market price/value, account cash via `accountSummary` TotalCashValue USD). Full-mirror semantics per account, same as RH.

**Steps:**
- [ ] Failing tests using hand-built fake `PortfolioItem`/`Contract` objects (no network): normalization for STK and OPT (conId stored, OCC fields), negative-position short option, upsert idempotency, cash extraction.
- [ ] Implement; wire `gateway` to fire `ibkr_sync.run(engine, ib)` after successful connect and on a 15-min loop guarded by `gateway.connected`.
- [ ] Verify: suite green; worker boots with gateway down → no errors, no ibkr rows (confirmed in logs).
- [ ] **Live verification: BLOCKED on TODO.md item** (paper account activation). When unblocked: bring up ib-gateway on VPS, run one sync, confirm ibkr rows join the dashboard.
- [ ] Commit: `Task 7: IBKR position sync module (mock-verified, activates with gateway)`.

---

## Task 8 — Deploy + live verification (RH path)

**Steps:**
- [ ] **[USER ACTION]** Task 0 RH bootstrap: run `rh_login.py` locally (MFA), `scp secrets/rh-session.pickle root@204.168.169.27:/root/broker-cockpit/secrets/`.
- [ ] VPS: `git pull`, `docker compose -f compose.yml -f compose.prod.yml up -d --build`.
- [ ] **[per-action OK]** Trigger `POST /internal/sync/robinhood` in-container; expect real position counts; check `sync.robinhood.ok` audit row.
- [ ] Trigger `POST /internal/snapshots/run`; confirm snapshot row.
- [ ] Browser: cockpit.gavinong.org shows real RH positions, allocations, day change; position detail works; (chart shows 1 point — expected).
- [ ] Remove/guard demo seed data if it was ever loaded in prod (it must never have been — verify).
- [ ] Update `TODO.md`: Phase 1 live on RH; IBKR line-item unchanged.
- [ ] Commit: `Task 8: phase 1 live on Robinhood mirror`.

---

## Task 9 (optional) — UW options enrichment

Only if `UW_API_KEY` provided. Worker fetches greeks/IV for held option contracts during sync (direct REST, keyed by OCC symbol), stores in a new `option_metrics` table (migration 0003), API exposes them, option rows in the UI show IV/delta chips. Graceful skip when key absent. Full TDD same pattern as above. **[per-action OK for first live UW call]**

---

## Self-review

- **Spec coverage (§4 Phase 1):** RH mirror poll ≈15min ✓ (T1/T2); aggregated positions w/ per-broker lots ✓ (T1/T4/T5); options shown day 1 ✓ (T1/T5); greeks/IV context → T9 (optional, honest degradation — spec's UW dependency preserved but not blocking); snapshots + value chart ✓ (T3/T6); position detail page ✓ (T6); allocation views ✓ (T5); IBKR stream — module built T7, live activation externally blocked (TODO.md), consistent with reality.
- **Placeholder scan:** journal section on detail page is an explicit labeled Phase-2 placeholder (by design, spec §5). No TBDs elsewhere.
- **Consistency:** `SyncResult` used by T2 endpoint response and T8 verification; staleness thresholds identical in T2 (writer) and T4 (reader) — define once in `scheduler.py` and import in `portfolio_api.py`; masking semantics defined once in `format.ts` and used by T5+T6; snapshot timing reuses the extracted heartbeat helper (T3 explicitly forbids duplication).
