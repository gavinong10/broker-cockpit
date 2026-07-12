# Value history: backfill + cash flows (deposits vs performance)

**Goal:** the portfolio value chart becomes honest history: (1) backfill
pre-go-live daily values from Robinhood's portfolio-equity records, (2) ingest
bank transfers into `cash_flows` so deposits stop looking like gains, (3) show
the deposits baseline on the chart with a performance-excluding-deposits stat.

## Tasks (review gate after each)
1. **Migration + backfill** — `snapshots.source` column ('observed' default |
   'backfill_rh'); `app/value_history.py backfill_snapshots` inserts ONLY
   missing days from RH `get_historical_portfolio` (never overwrites observed
   rows); POST /internal/snapshots/backfill (one-time, manual).
2. **Cash-flow ingestion** — `sync_cash_flows` upserts completed RH bank
   transfers into `cash_flows` idempotently via `source_ref = rh-ach:{id}`
   (schema was designed for this); runs daily in the snapshot loop; POST
   /internal/cashflows/sync for manual runs; GET /internal/cashflows (per-day
   net) for the UI. ACATS/wire not exposed by robin_stocks — out of scope,
   enter manually if ever needed.
3. **Chart treatment** — ValueChart overlays a dashed net-deposits baseline
   (first snapshot value + cumulative flows since) and a "performance excl.
   deposits" stat; backfilled history disclosed in a footnote.

Fetchers are injectable for tests (same pattern as plan_monitor.quote_fn).
Read-only w.r.t. trading throughout.
