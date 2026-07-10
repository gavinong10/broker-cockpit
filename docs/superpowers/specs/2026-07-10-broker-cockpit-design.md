# broker-cockpit — Design Spec

**Date:** 2026-07-10
**Status:** Approved section-by-section via brainstorming session; this document is the umbrella design. Each phase gets its own implementation plan when work begins.
**Working name:** `broker-cockpit` (rename freely).

---

## 1. Purpose

A single-tenant personal webapp giving a unified, Robinhood-feel view over positions held at Robinhood and Interactive Brokers, with all order execution on IBKR. Adds what RH cannot do: trade annotation with search, advanced rules (trailing stops, price-conditional options orders, IV-based triggers), and allocation-based DCA/rebalancing with drift diffs. Owner has full control; invited guests get read-only access.

### Standing safety posture (human-gated default)

The system holds live broker credentials and places real orders. **Every non-protective order path is propose-and-confirm by default.** Only protective rules (trailing stops, stop-losses) approved once at creation fire autonomously. Any rule can be individually flipped to full-auto by explicit owner opt-in, subject to hard notional caps. Every broker-affecting event lands in an append-only audit log.

---

## 2. Decisions log (from brainstorming)

| Decision | Choice |
|---|---|
| Robinhood's role | **Read-only mirror.** No order placement via unofficial API (ToS/lockout risk). All execution on IBKR. |
| Deployment | **Single-tenant cloud VPS** (~$10–20/mo), docker-compose. Guests are read-only logins to the owner's instance. |
| Rule gating | **Tiered.** Protective = approve-at-creation, fire autonomously. Opportunistic (IV triggers, entries, DCA) = propose-and-confirm per fire. Per-rule override to full-auto with caps. |
| Build order | Read-only unified view → trading + journal → rules engine → DCA/rebalancer. |
| Options scope | View (with greeks/IV) from day 1; **single-leg** trading in Phase 2 (incl. CSP/CC helpers); multi-leg deferred. |
| Architecture | **Next.js UI + Python worker + dockerized IB Gateway + Postgres** (Approach A). |
| IBKR auth | **Headless secondary IBKR user from day 1**: trading-only permissions (no funding/withdrawals/settings), opted out of Secure Login System → zero-touch re-auth. Primary user keeps full 2FA and is never stored. |
| Discord signal listener | **Deferred.** Adapter interface + generic webhook ship in Phase 3; Discord adapter is a future plug-in. |
| Currency stance | **USD-native accounting** over multi-currency plumbing (see §10). |

---

## 3. Architecture

One VPS, docker-compose, four services:

| Service | Role |
|---|---|
| `web` (Next.js) | RH-style UI. Google login via Auth.js. Talks only to Postgres and the worker's internal API. |
| `worker` (Python / FastAPI) | All broker connectivity (`ib_async` to gateway; RH sync), rules engine, schedulers (snapshots, RH poll, DCA proposals, FX sweep), notifications (Discord webhook + push). Not publicly exposed — reachable only on the docker network. |
| `ib-gateway` | Standard dockerized IB Gateway (gnzsnz image + IBC), auto-restart daily, headless secondary-user login. |
| `postgres` | Positions cache, journal, rules, proposals, audit log, allocation models, snapshots. |

### Trust boundaries

- Browser never sees broker credentials and never talks to brokers.
- Broker credentials (headless IBKR user, RH session token) live in `.env`/docker secrets on the VPS. Never in DB, never in repo.
- Worst-case credential theft = unwanted trades, not moved money (headless user cannot withdraw or change settings).

### Identity & roles

- Google OAuth (Auth.js). Email allowlist table → role. Owner email → `owner`; invited emails → `viewer`; others rejected at login.
- `viewer`: read-everything, write-nothing — enforced server-side on every route/action. Optional per-viewer flag masks dollar amounts (percent weights only).
- `owner`: order placement requires active session + per-fire confirm for anything non-protective.

### Failure posture

- Gateway session drop → Discord alert; IBKR-native orders keep protecting positions; worker-plane rules pause and the UI shows "paused — no data" (never evaluates stale quotes silently).
- RH session expiry (~monthly MFA) → mirror marked stale + re-auth nudge; nothing else degrades.
- IBKR weekly/daily restarts are handled by IBC auto-restart; headless user makes re-auth zero-touch.

### Budget

VPS $10–20/mo + IBKR market data (US + KRX lines) ~$5–20/mo + Backblaze B2 backups ~$1/mo. Unusual Whales already subscribed. Total new spend well under the $150/mo ceiling; headroom for Polygon later if a data gap appears.

---

## 4. Phase 1 — Unified portfolio view

**Data flow:** worker holds a live `ib_async` subscription (positions, fills, quotes stream in real time). RH mirror polls every ~15 min during market hours via unofficial API. Both normalize into one `positions` model keyed by instrument, with per-broker lots underneath.

**UI (RH-feel):**
- Total value + day change + value-over-time chart from daily snapshots. Honest caveat: history accumulates from go-live; backfill best-effort (IBKR Flex reports decent, RH thin).
- Position list aggregated by ticker across brokers, expandable to per-broker lots. Equities and options both shown; option rows carry greeks/IV/IV-rank from Unusual Whales (US-only; non-US names degrade gracefully without it).
- Position detail page: chart, lots, cost basis, realized/unrealized P/L, and the position's full journal thread.
- Allocation views by ticker / sector / asset class (foundation for Phase 4).

### 4.1 Performance calculation (fixing the Robinhood skew)

RH-style "value vs net contributions" breaks under withdrawals (e.g. $100k in → +$100k profit → $100k out shows nonsense). We show three clearly-labeled measures:

- **TWR (chart y-axis):** time-weighted return, chain-linked daily from snapshots with flow adjustment `r_t = (V_t − flow_t) / V_{t−1}`. Immune to deposit/withdrawal distortion; answers "how did the strategy perform per deployed dollar." Period selectors re-base the index. Index pauses (not ÷0) if account value ≈ 0.
- **XIRR (stat block):** money-weighted IRR over the external cash-flow ledger; answers "what did my dollars earn given my contribution timing." Annualized scalar; the TWR-vs-XIRR gap surfaces timing skill/luck.
- **Dollar P/L:** always shown alongside percentages.

Rules: only **external** flows (deposits, withdrawals, ACATS) count as flows — dividends/interest are returns, not contributions. End-of-day flow convention so a deposit never reads as same-day gain. Flows detected from broker activity streams (IBKR + RH mirror).

---

## 5. Phase 2 — Trading + journal (IBKR only)

### Order ticket

- **Stocks:** shares-or-dollars toggle (fractional for US listings), market / limit / stop / stop-limit / **trailing stop**, bracket orders, price-conditional orders — all IBKR-native, resting server-side at IBKR. Simple defaults up front; advanced types behind a "more" expander.
- **Preview before confirm:** IBKR what-if gives real estimated commission + margin impact on the preview screen. One explicit confirm submits.
- **Single-leg options:** chain browser (expiry tabs, strikes around ATM; bid/ask, IV, delta, OI per row). Buy/sell calls and puts. Helper modes: cash-secured put (settled-cash coverage check) and covered call (share coverage check).
- **Sell sensibility defaults:** specific-lot selection with long/short-term tax hint per lot and estimated tax delta; warns if a sell breaks an active rule or covered-call collateral (and, once Phase 4 ships, an allocation target); quantity pre-filled from position.

### Journal

- Every in-app order requires a "why": one required tag from an owner-defined taxonomy (e.g. `iv-crush`, `dca`, `earnings-play`, `trim`) + optional free text, thesis link, target/stop, confidence.
- **Signal provenance field** on orders and journal entries: which rule / source / message caused this trade (enables "Discord-sourced vs manual performance" queries later).
- Trades made outside the app (RH activity, IBKR orders from TWS/mobile) are caught by reconciliation → **unannotated queue** with a nagging badge.
- Position threads: entries chain across open → adds → trims → close; closed positions read as a story stamped with realized P/L.
- Search: Postgres full-text over notes + filters (ticker/tag/date/outcome). Later: outcome stats by tag.

---

## 6. Phase 3 — Rules engine, signals, approvals

### Core abstraction: Sources → Signals → Rules → Proposals → Orders

Every trigger origin is an **adapter** emitting normalized `SignalEvent`s (source, instrument, payload, timestamp). The engine matches events against rules and is source-agnostic. A new strategy source later = one new adapter; the engine never changes.

**Phase-3 adapters:**
1. **Market data** — underlying price / % moves, option mid, IV / IV-rank / greeks (UW), evaluated on a ~1-min loop over held + watched instruments.
2. **Broker events** — fills, assignments, position opens/closes (streamed). Enables e.g. "CSP assigned → propose covered call."
3. **Time** — schedules (feeds Phase-4 DCA).
4. **Generic inbound webhook** — authenticated endpoint so any external script can inject signals.

**Deferred adapter:** Discord channel listener (message → LLM classifier → structured signal). Designed-for via the adapter interface; not built in v1.

### Rule anatomy

Trigger condition (composable AND/OR over signal fields) + action template (order draft + sizing policy: fixed $, % of portfolio, contracts) + gating tier + constraints (max position size, cooldown, market-hours-only per exchange calendar, rule expiry date).

**Two execution planes:**
- **IBKR-native plane:** anything expressible as a native order (trailing stop, stop-limit, price-conditional) compiles down to an order resting at IBKR. Survives VPS death.
- **Worker plane:** everything else (IV conditions, webhook signals, composites) evaluated in the worker loop.

### Approval flow

Rule fires → order drafted → what-if preview attached → Discord rich-embed notification (rule name, triggering signal, proposed order, preview numbers) + in-app pending inbox → authenticated confirm page (owner only) → one tap submits → outcome posted back. **Proposals expire** (default 15 min, or earlier if price drifts past a set bound) — a stale approval can never execute at an unseen price.

### Guardrails

- Global kill switch: pauses all firing, cancels pending proposals.
- Notional caps: per-rule and global daily/weekly, enforced in the worker regardless of rule config.
- **Dry-run mode:** every new rule starts in paper mode logging would-have-been proposals; owner reviews the record, then arms it. (Exit gate for Phase 3: first rule survives 2 weeks of dry-run.)
- Dedup/cooldown: same signal cannot re-fire a rule within its cooldown.
- Full audit: every signal, firing evaluation, proposal, approval/rejection/expiry, and order result in the append-only log.

---

## 7. State, durability, crash recovery

**Governing principle: the broker is the source of truth for money; Postgres is the source of truth for meaning.**

1. **Reconstructable state → reconcile on startup.** Worker boot pulls positions, cash, open orders, recent executions from IBKR (and RH) and rebuilds its live picture before evaluating any rule. A crash can leave us briefly ignorant, never wrong.
2. **Irreplaceable state → Postgres + off-VPS backups.** Journal, rules, allocation models, proposals, audit log. Nightly `pg_dump` → Backblaze B2, 30-day retention. **Documented restore drill is a Phase-0/1 deliverable.**
3. **In-flight orders → idempotency tags.** Proposal state machine, each transition a DB transaction:

   `created → notified → approved → submitting → submitted → filled / cancelled / expired`

   Before submission the proposal moves to `submitting` with a UUID that rides on the IBKR order's `orderRef`. On restart, any `submitting` proposal is resolved by querying IBKR for that ref: found → mark submitted; absent → safe to retry (or re-propose if the price bound expired). Double submission is structurally impossible; every IBKR fill is permanently joinable to its rule, proposal, and journal entry.
4. **Rule state in DB, not memory:** cooldowns, last-fired, dry-run logs survive restarts; no reset-and-double-fire.
5. **Honest gap:** signals occurring entirely during downtime are missed, not queued. Mitigations: prefer **level-based** conditions ("IV rank *is* < 30") over edge-based ("crosses below") — levels re-evaluate correctly on recovery; downtime itself alerts via Discord; protective orders are IBKR-native and unaffected. Missed daily snapshots leave a chart gap, backfillable from Flex reports.

---

## 8. Phase 4 — DCA / rebalancer

### Target allocations

- **Model portfolio:** tickers with target weights (flat list v1). **Versioned** — every edit is a new version; "reset" shows diff of old targets vs new targets vs current actual, fine-tune, commit. Ideal-portfolio evolution is queryable history.
- **Sleeves:** every position tagged `core` or `tactical`. Rebalancer measures drift against, and trades, **core only**. Rule-placed and manual-ticket trades auto-tag `tactical`; DCA/rebalance buys auto-tag `core`; owner can retag any position (lot-level, so 100 AAPL core + 40 AAPL tactical coexist). Untagged defaults to `tactical` (safe direction). Dashboard offers both lenses: total portfolio, and core-vs-target.
- **Measure both brokers, trade IBKR only:** RH shares count toward current weights; diff view flags drift stuck in RH that buying alone can't fix.

### DCA flow

Cash lands at IBKR (detected or scheduled) → **buy-only** allocation toward most-underweight core names (fractional for US listings; whole-share rounding for non-US, remainder carried to next run). One batch proposal; review screen allows per-line edit/exclude/skip; one confirm submits.

### Full rebalance (with sells)

Manual trigger, or suggested when drift exceeds owner-set threshold (e.g. ±5%). Tax-lot-aware sells: losses first, then long-term gains; short-term-gain sales flagged loudly with estimated tax impact per line. Cross-checks active rules (won't silently propose selling covered-call collateral). Same batch-review-confirm UX. Rebalance/DCA orders are **always propose-and-confirm** — never auto.

---

## 9. Multi-currency plumbing

IBKR launched direct KRX (Korean) equities access May 2026; enabled per-account via trading permissions + market data. Trades settle in KRW at ~0.2bp FX spreads; foreign balances holdable.

- Every instrument, cash balance, and lot carries a `currency`. Live FX from IBKR's forex feed (IDEALPRO quotes, free).
- Each instrument carries its exchange calendar (market-hours constraints, quote-loop scheduling respect it; worker runs 24h).
- KRX market data is one more small IBKR subscription line.
- UW context (IV/greeks) is US-only; non-US rows degrade gracefully.

## 10. USD-native accounting (resolved stance)

The owner thinks in USD; the system adopts the stance of a USD investor. Multi-currency machinery (§9) stays under the hood.

1. **USD cost basis & P/L:** foreign buys record USD cost at execution (what actually left the account). P/L = USD-in vs USD-out-now — currency move included, because that is the real economics. Asset-vs-FX decomposition demoted to an expandable detail row.
2. **Auto-sweep foreign cash:** housekeeping rule converts any non-USD balance above a small threshold back to USD. Currency-neutral plumbing → protective tier (approve rule once, runs autonomously, every sweep audited). Cash is always one USD number.
3. **USD-first ticket:** type dollars for any instrument; shares computed (whole-share rounding shown for non-US). FX conversion appears as one preview line.
4. **Currency-agnostic rule conditions:** for foreign names, default condition types are percent-from-entry / percent-move (currency-free). Local-currency absolute triggers available under an advanced toggle.

Accepted side effect: a foreign stock's USD chart wobbles slightly with FX even when its home exchange is closed — that is the position's actual value moving, not an artifact.

---

## 11. Phase plan

| Phase | Delivers | Exit criterion |
|---|---|---|
| **0 — Skeleton** | VPS + compose, headless IBKR user + gateway auth, Google login w/ roles, DB schema, backups **+ restore drill** | Log in works; gateway survives a week unattended |
| **1 — Unified view** | IBKR stream + RH mirror, aggregated positions, options w/ UW greeks, snapshots, position pages | Owner stops opening RH/IBKR apps to check portfolio |
| **2 — Trading + journal** | Order ticket (stocks + single-leg options; native trailing/bracket/conditional), what-if preview, required "why", unannotated queue, search | Owner places real trades here by default |
| **3 — Rules engine** | Market-data / broker-event / time / webhook adapters, proposal + approval flow, guardrails, dry-run | First rule survives 2 weeks of dry-run and is armed |
| **4 — DCA/rebalancer** | Models + versioning, sleeves, buy-only DCA, tax-aware rebalance | First real DCA batch confirmed and filled |

Each phase begins with its own superpowers `writing-plans` cycle against this spec.

## 12. Deferred / non-goals

- **Discord signal listener adapter** (message → LLM classifier → signal) — interface designed, not built.
- **Multi-leg options** (spreads, atomic rolls) — until a real need shows up.
- **Robinhood order placement** — permanently out (ToS/lockout risk).
- **Multi-tenancy** — single-tenant by design; guests are viewers on the owner's instance.
- **Local-currency accounting views** — USD-native stance; decomposition detail row only.

## 13. Known risks

| Risk | Mitigation |
|---|---|
| RH unofficial API breaks or locks account | Read-only usage minimizes surface; mirror marked stale on failure; app still fully functional for IBKR. |
| IB Gateway session flakiness | IBC auto-restart + headless SLS-opt-out user; native orders survive outages; disconnect alerts. |
| Headless credential theft | Trading-only permissions; no funding/withdrawal/settings rights; VPS hardening; secrets never in DB/repo. |
| Rules engine bugs killing good trades or firing bad ones | Dry-run gate, notional caps, kill switch, propose-and-confirm default, full audit; level-based conditions preferred. |
| Double order submission on crash | `orderRef` idempotency + proposal state machine (§7.3). |
| Missed signals during downtime | Level-based conditions, downtime alerting; protective orders unaffected (IBKR-native). |
