# Baskets + Conversation-Import Implementation Plan

> **For agentic workers:** execute task-by-task with review gates. Read CLAUDE.md
> and TODO.md at the repo root before starting any task.

**Goal:** Named trade baskets ("campaigns") that act as sub-portfolios — own thesis,
own cost basis, own daily value history, own UI lens — plus a documented, reusable
local capability that turns a Claude Code **conversation ID** into a live basket:
`uv run python scripts/import_basket.py <session-id>` extracts the thesis from the
transcript, drafts a basket manifest via headless `claude -p`, matches it against
synced positions, and pushes it to the deployed worker over SSH.

**Architecture:** Baskets generalize the spec's sleeves: `core` is implicit (any
unallocated quantity), named baskets hold quantity-sliced allocations against
instruments. Worker owns basket CRUD/accounting; snapshots job writes per-basket
value rows; portfolio API grows basket filters; web adds basket cards + a scoped
basket view reusing existing components. The conversation-import pipeline is a LOCAL
capability (transcripts only exist on the Mac): locate JSONL → extract text → LLM
manifest (strict JSON schema) → push to worker `/internal/baskets/import` via SSH.

**Capability doc (first-class deliverable):** `docs/capabilities/conversation-import.md`
describing the generic pipeline (transcript locator, extractor, manifest schema,
LLM invocation pattern, SSH push) so future scripts (rule-sets from conversations,
research imports) reuse `scripts/lib/conversation.py` rather than reinventing it.
CLAUDE.md gets a pointer.

## Global constraints
- RH stays read-only; baskets never place orders. Import only labels existing synced positions.
- Viewer masking rules apply to every new UI surface (dollars + qty masked; percents real).
- The import script runs manually by the owner on the Mac (that run IS the per-action
  consent for its one `claude -p` call and SSH push).
- Same-ticker-in-multiple-baskets must work via qty slices; over-allocation must be rejected.

---

## Task A — Worker: baskets schema, accounting, API

**Migration 0003:**
- `baskets(id, slug unique, name, thesis text, source_ref text null, horizon text null,
  invalidation text null, status text default 'open', created_at timestamptz default now())`
- `basket_allocations(id, basket_id fk, instrument_id fk, qty numeric(24,8),
  cost_basis_usd numeric(18,4) null, created_at, UNIQUE(basket_id, instrument_id))`
- `basket_snapshots(id, basket_id fk, taken_on date, value_usd numeric(18,2),
  UNIQUE(basket_id, taken_on))`

**Module `app/baskets.py`:**
- `create_basket(engine, manifest) -> dict` — manifest: `{slug, name, thesis, source_ref?,
  horizon?, invalidation?, legs: [{symbol_or_underlying, sec_type: "OPT"|"STK", qty?: str}]}`.
  Matching rule for allocations: for each leg, find positions whose instrument matches —
  OPT legs match ALL option positions on that underlying (parsed from OCC symbol prefix)
  unless a full OCC symbol is given; STK legs require explicit qty (slice of the stock
  position). Allocation qty = min(requested or full position qty, unallocated remainder
  across other baskets). Reject (400, listing conflicts) if any leg over-allocates.
  cost_basis_usd = current avg_cost_usd × qty × multiplier at allocation time.
- `basket_value(engine, basket_id) -> Decimal` — Σ qty × last_price × multiplier.
- Extend `app/snapshots.py`: after the portfolio snapshot, upsert one `basket_snapshots`
  row per open basket (same taken_on idempotency).

**API (internal-auth, Decimals as strings):**
- `POST /internal/baskets/import` — manifest in, created basket + allocations out.
- `GET /internal/baskets` — list w/ name, thesis, status, deployed (Σ cost_basis),
  current value, pl_usd, pl_pct, nearest option expiry across allocations.
- `GET /internal/baskets/{slug}` — detail: basket fields + positions (same row shape as
  /internal/portfolio positions, scoped to allocation qty) + snapshots (ascending).
- `DELETE /internal/baskets/{slug}` — closes (status='closed'), keeps history.
- `/internal/portfolio` gains `basket` chips: each position row includes
  `baskets: [{slug, qty}]` for its allocations (empty = core).

**TDD:** pg-gated tests on cockpit_test (see CLAUDE.md): manifest→allocations matching
(OPT-by-underlying incl. multiple expiries, STK qty slice, over-allocation 400,
second basket takes remaining qty), basket value math, snapshot idempotency, list/detail
shapes, 401s. Full suite green both modes. Commit.

## Task B — Local capability: conversation → basket manifest → push

**`scripts/lib/conversation.py`** (reusable):
- `find_transcript(session_id) -> Path` — searches `~/.claude/projects/*/<id>.jsonl`.
- `extract_text(path, max_chars=150_000) -> str` — user messages + assistant text blocks
  (skip tool calls/results), chronological, truncating LONG assistant blocks to heads,
  keeping ALL user messages (they carry intent), tail-biased when over budget.
- `run_claude_json(prompt, schema_hint, timeout_s=300) -> dict` — invokes headless
  `claude -p <prompt> --output-format json`, parses the result field, extracts the
  first JSON object, validates required keys, returns dict. Raises with stderr excerpt
  on failure.

**`scripts/import_basket.py <session-id> [--since YYYY-MM-DD] [--dry-run]`:**
1. Locate + extract transcript.
2. Prompt `claude -p` for the basket manifest (exact JSON schema from Task A, with
   slug/name/thesis/horizon/invalidation/legs; instruct: legs = instruments the
   conversation concluded should be TRADED, underlyings only unless specific contracts
   were named; source_ref = the session id).
3. Show the manifest to the operator, confirm y/n (skip with --yes).
4. Push: `ssh root@204.168.169.27 'docker compose -f ... exec -T worker python -c ...'`
   posting to `/internal/rh/... /internal/baskets/import` in-container with the token
   from the VPS env (never copy the token locally). `--dry-run` prints the manifest
   and the matching preview (call import with a `dry_run: true` flag — Task A supports
   it: compute allocations, return them, write nothing).
5. Print resulting allocations + the basket URL (https://cockpit.gavinong.org/baskets/<slug>).

**`docs/capabilities/conversation-import.md`** (the documented capability):
- What it is, when to use it, the generic pipeline diagram, the manifest-schema pattern,
  how to write a NEW importer in <50 lines reusing `scripts/lib/conversation.py`
  (worked example: "import a rules-set from a conversation" sketch), operational notes
  (transcripts are local-only; `claude -p` uses the operator's subscription; the manual
  run constitutes per-action consent; SSH push pattern; dry-run first).
- CLAUDE.md: add a one-line pointer under a "Capabilities" heading.

**Verify:** unit tests for extractor (fixture JSONL) + manifest JSON parsing (mock the
claude CLI call — no live LLM call in tests); a `--dry-run` against the REAL session
5a6b9ddd-490e-4ae6-91c7-74db07e4140f is the live acceptance check (operator-run). Commit.

## Task C — Web UI: basket cards + basket view

- Dashboard: "Baskets" section under the header (only when ≥1 basket): cards with name,
  thesis (truncated, expandable), deployed vs current value, P/L $ / %, nearest-expiry
  runway chip ("72d to nearest expiry"), all masked per role (deployed/current/P/L $
  masked; P/L % real).
- `/baskets/[slug]` page: thesis + invalidation + horizon block (text — visible to all
  roles), source_ref line, then the scoped mini-portfolio: allocation bars by position,
  position table (reuse components, feed basket-scoped rows), basket value chart from
  basket_snapshots (reuse ValueChart).
- Position rows on the main table get small basket chips (slug) linking to the basket page.
- vitest for any new pure helpers; build clean; deploy.

## Sequencing
A → B and C in parallel (disjoint trees) → deploy → live acceptance: dry-run import of
session 5a6b9ddd..., then real import after Monday's fills sync.
