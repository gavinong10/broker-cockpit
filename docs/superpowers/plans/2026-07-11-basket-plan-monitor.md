# Basket Plan Monitor — pending purchases, visualized and graded

**Goal:** baskets gain a *plan* layer: per-leg target structures (option verticals or
stock) with planned entry economics, monitored every sync cycle against live quotes.
The UI answers "is entering now optimal or suboptimal vs. the plan?" and, after fill,
"did we execute the plan, and how is it tracking?"

**Origin:** strategy session `5a6b9ddd-490e-4ae6-91c7-74db07e4140f` produced an
11-leg thesis-anchored call-spread portfolio (~$21.3k planned debit, per-leg planned
costs/breakevens/tolerances). Baskets today only label *owned* positions
(`baskets.py`: "Baskets never place orders — they only label existing synced
positions"). Plans represent *intended* positions — strictly read-only w.r.t.
trading; the propose-and-confirm boundary (design spec §3) is untouched.

## Leg lifecycle

`pending → partial → held` (graduation via the existing allocation matcher when a
synced position matches the plan's contracts) · `abandoned` (manual).

## Monitor statuses (computed each sync cycle for pending legs)

- `in_window` — live structure cost ≤ planned_net_debit × (1 + tolerance_pct/100)
- `drifted` — above tolerance; payload decomposes drift into underlying-move vs
  IV-move components
- `thesis_stale` — underlying moved beyond structure validity (spot > long strike
  ⇒ the modeled entry no longer exists); recommendation: re-derive, don't chase
- `unquotable` — no usable quotes (dead chain); shows last-known net + age

## Tasks (review gate after each)

1. **Schema + models** — `basket_plan_legs` (structure as JSONB contract list with
   ratios, qty, planned_net_debit, tolerance_pct, planned economics for display,
   status, alert-dedupe state) and `basket_plan_marks` (per-cycle net-cost history
   for drift sparklines). Additive migration off `3f8ad2283ded`. No behavior change.
2. **Plan CRUD + import** — `app/plans.py` (validate/create/list; OCC parsing reuses
   `baskets.parse_underlying`), `POST /internal/baskets/{slug}/plan`,
   manifest-schema extension (optional `plan` block per leg — backward compatible).
3. **Quote engine + scheduler hook** — `app/plan_monitor.py`: quote each pending
   structure via the existing robin_stocks session (read-only market-data calls by
   contract spec), net the legs, compute status + drift decomposition, write a mark
   row, persist status transitions. Called from `scheduler.py` sync loop after the
   position sync; exception-guarded like the rest of the loop.
4. **Alerts** — `notify.alert` embeds on status *transitions* only (dedupe via
   `last_alerted_status`): entry-window opened, drift crossed, thesis stale.
   Existing Discord webhook/channel (config-compatible; separate webhook env var
   optional later).
5. **Read API** — `GET /baskets/{slug}/plan`: legs + latest marks + mark history +
   precomputed payoff-curve points (server-side; client renders SVG only).
6. **Web UI** — `Plan` tab on the basket page: leg cards with status chips and
   Δ-vs-plan, drift sparkline, basket payoff curve, fill-quality scorecard for
   held legs (actual fill vs plan), planned-vs-filled progress header, tripwire
   list (from basket manifest `invalidation`/notes).
7. **conversation-import extension + real import** — extend
   `scripts/import_basket.py` prompt schema with the `plan` block; re-import
   session `5a6b9ddd…` as basket `ai-not-a-bubble-yet` with all 11 planned legs.
8. **Graduation** — matcher: when synced positions cover a pending leg's contracts,
   flip status to partial/held, capture actual fill cost from allocation
   `cost_basis_usd`, emit fill-quality alert (slippage vs plan).

## Decisions taken (flag at review if disputed)

- Quote source: **Robinhood session already in the worker** (zero new deps/keys).
  UW enrichment stays a separate future task (TODO's Task 9).
- Structure unit: one plan leg row = one *structure* (e.g. a 2-contract vertical),
  not one contract — economics (net debit, breakeven, max value) are
  structure-level facts.
- Alerts reuse the existing Discord channel.
- Plans never auto-place or auto-cancel anything.

## Verification

- Postgres-gated tests against `cockpit_test` (per CLAUDE.md), covering: manifest
  validation, net-cost math (incl. short-leg credit), status classification
  boundaries (tolerance edge, thesis-stale edge), drift decomposition arithmetic,
  graduation matching, alert dedupe.
- Manual: seed the 11-leg plan locally, run one monitor cycle against recorded
  quote fixtures, view the Plan tab in local dev (`docker-compose`, hyphen).
