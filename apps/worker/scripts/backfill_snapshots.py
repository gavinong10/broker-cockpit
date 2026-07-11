"""Portfolio-value history backfill (estimated, pre-go-live only).

Reconstructs daily portfolio snapshots for every day BEFORE the first real
snapshot (2026-07-11) by walking today's known positions + cash backward
through the complete Robinhood order/dividend/transfer history, pricing each
day from historical closes. Written estimated rows are flagged
``"estimated": true`` and never overwrite real snapshot rows.

READ-ONLY DISCIPLINE (absolute): this script calls robin_stocks *getters* only
— never any order/trade/cancel endpoint. Grep-provable: the only robin_stocks
names referenced are get_* / load_* getters and helper.request_get.

Runs INSIDE the worker container on the VPS (the session pickle lives there).
Invoked manually:

    uv run python scripts/backfill_snapshots.py --dry-run          # compute + summary, write nothing
    uv run python scripts/backfill_snapshots.py --dry-run --from-cache
    uv run python scripts/backfill_snapshots.py --execute          # write snapshots + cash_flows

Raw pulls are cached to /tmp so re-runs (and --execute after --dry-run) do not
re-hit Robinhood: pass --from-cache to reuse them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Ensure the worker project root (…/apps/worker) is importable when this file is
# run directly as a script (python scripts/backfill_snapshots.py), in which case
# sys.path[0] is the scripts/ dir, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

# --- constants ----------------------------------------------------------------

# The first REAL (live) snapshot. Backfill writes strictly BEFORE this date and
# never touches this row or anything on/after it.
FIRST_REAL_SNAPSHOT = date(2026, 7, 11)
# Anchor day: "today" for the walk-back is the last pre-go-live day.
ANCHOR_DATE = FIRST_REAL_SNAPSHOT - timedelta(days=1)  # 2026-07-10

OPTION_MULTIPLIER = Decimal("100")
CACHE_DIR = Path("/tmp/backfill_snapshots")
API_SLEEP = 0.3  # polite pause between historical calls
_CENT = Decimal("0.01")
_QTY_EPS = Decimal("0.0001")  # residual tolerance for "≈ zero" holdings

# terminal states we still count toward cash (settled money moved)
_TRANSFER_DEAD = {"cancelled", "canceled", "failed", "rejected", "reversed"}
_DIVIDEND_PAID = {"paid", "reinvested"}


# --- pure data types ----------------------------------------------------------

@dataclass
class Fill:
    """One holdings-changing execution, in FORWARD (chronological) direction."""
    occurred_on: date
    key: str            # ticker (STK) or OCC symbol (OPT)
    sec_type: str       # STK | OPT
    qty_delta: Decimal  # signed: + increases holdings, - decreases
    cash_delta: Decimal # signed: + cash in, - cash out (fees folded in)


@dataclass
class CashEvent:
    """A cash-only event (dividend, bank transfer), forward direction."""
    occurred_on: date
    cash_delta: Decimal
    kind: str           # dividend | deposit | withdrawal


@dataclass
class DaySnapshot:
    taken_on: date
    holdings: dict       # key -> signed qty (nonzero only)
    cash: Decimal


@dataclass
class OptMeta:
    underlying: str
    expiry: date
    right: str          # C | P
    strike: Decimal
    multiplier: Decimal = OPTION_MULTIPLIER


# --- OCC parsing --------------------------------------------------------------

_OCC_RE = re.compile(r"^(?P<u>[A-Z0-9]+?)(?P<d>\d{6})(?P<r>[CP])(?P<s>\d{8})$")


def parse_occ(occ: str) -> OptMeta:
    """Parse an OCC symbol produced by robinhood.occ_symbol back to its parts."""
    m = _OCC_RE.match(occ)
    if not m:
        raise ValueError(f"not an OCC symbol: {occ!r}")
    expiry = datetime.strptime(m.group("d"), "%y%m%d").date()
    strike = Decimal(m.group("s")) / 1000
    return OptMeta(m.group("u"), expiry, m.group("r"), strike)


def _occ(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    return f"{underlying}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"


def _dt_date(ts: str | None) -> date | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def _dec(v) -> Decimal:
    return Decimal("0") if v in (None, "") else Decimal(str(v))


# --- pure: build fills / cash events from raw pulls ---------------------------

def build_stock_fills(orders: list[dict], symbol_by_url: dict[str, str]) -> list[Fill]:
    """Reverse-engineer share deltas + cash from stock orders. Uses per-execution
    rows when present (accurate dates/prices), else the filled summary."""
    fills: list[Fill] = []
    for o in orders:
        state = o.get("state")
        executions = o.get("executions") or []
        if state not in ("filled", "cancelled") or not (executions or state == "filled"):
            continue
        if state == "cancelled" and not executions:
            continue
        symbol = symbol_by_url.get(o.get("instrument", ""))
        if not symbol:
            continue
        sign = Decimal("1") if o.get("side") == "buy" else Decimal("-1")
        fees = _dec(o.get("fees"))
        if executions:
            for ex in executions:
                d = _dt_date(ex.get("timestamp"))
                qty = _dec(ex.get("quantity"))
                price = _dec(ex.get("price"))
                if d is None or qty == 0:
                    continue
                fills.append(Fill(d, symbol, "STK", sign * qty, -sign * qty * price))
            # fees applied once, on the order's final day
            if fees:
                d = _dt_date(o.get("last_transaction_at")) or _dt_date(o.get("updated_at"))
                if d:
                    fills.append(Fill(d, symbol, "STK", Decimal("0"), -fees))
        else:  # filled, no execution detail
            d = _dt_date(o.get("last_transaction_at")) or _dt_date(o.get("updated_at"))
            qty = _dec(o.get("cumulative_quantity") or o.get("quantity"))
            price = _dec(o.get("average_price"))
            if d and qty:
                fills.append(Fill(d, symbol, "STK", sign * qty, -sign * qty * price - fees))
    return fills


def build_option_fills(
    orders: list[dict], opt_meta_by_url: dict[str, OptMeta]
) -> tuple[list[Fill], dict[str, OptMeta]]:
    """Reverse-engineer option-contract deltas + net cash. Holdings change is
    leg-level (side/ratio); net cash is order-level (direction/price)."""
    fills: list[Fill] = []
    occ_meta: dict[str, OptMeta] = {}
    for o in orders:
        if o.get("state") != "filled":
            continue
        d = _dt_date(o.get("last_transaction_at")) or _dt_date(o.get("created_at"))
        if d is None:
            continue
        proc_qty = _dec(o.get("processed_quantity") or o.get("quantity"))
        # order-level cash: debit = money out (negative), credit = money in
        price = _dec(o.get("price"))
        net = price * OPTION_MULTIPLIER * proc_qty
        direction = o.get("direction")
        cash_delta = -net if direction == "debit" else net
        legs = o.get("legs") or []
        cash_applied = False
        for leg in legs:
            meta = opt_meta_by_url.get(leg.get("option", ""))
            if meta is None:
                continue
            occ = _occ(meta.underlying, meta.expiry, meta.right, meta.strike)
            occ_meta[occ] = meta
            ratio = _dec(leg.get("ratio_quantity") or "1") or Decimal("1")
            leg_qty = proc_qty * ratio
            sign = Decimal("1") if leg.get("side") == "buy" else Decimal("-1")
            # attach the order's net cash to the first leg only
            lc = Decimal("0")
            if not cash_applied:
                lc = cash_delta
                cash_applied = True
            fills.append(Fill(d, occ, "OPT", sign * leg_qty, lc))
        if not cash_applied and cash_delta != 0:
            # no resolvable legs but cash moved — record cash-only under a synthetic key
            fills.append(Fill(d, f"__optcash__", "OPT", Decimal("0"), cash_delta))
    return fills, occ_meta


def build_cash_events(dividends: list[dict], transfers: list[dict]) -> list[CashEvent]:
    events: list[CashEvent] = []
    for div in dividends:
        if div.get("state") not in _DIVIDEND_PAID:
            continue
        d = _dt_date(div.get("paid_at")) or _dt_date(div.get("payable_date"))
        amt = _dec(div.get("amount"))
        if d and amt:
            events.append(CashEvent(d, amt, "dividend"))
    for t in transfers:
        if (t.get("state") or "").lower() in _TRANSFER_DEAD:
            continue
        d = _dt_date(t.get("created_at"))
        amt = _dec(t.get("amount"))
        if not d or amt == 0:
            continue
        direction = (t.get("direction") or "").lower()
        if direction.startswith("withdraw"):
            events.append(CashEvent(d, -amt, "withdrawal"))
        else:  # deposit / received
            events.append(CashEvent(d, amt, "deposit"))
    return events


# --- pure: walk-back ----------------------------------------------------------

def walk_back(
    anchor: dict[str, Decimal],
    cash_today: Decimal,
    fills: list[Fill],
    cash_events: list[CashEvent],
    start: date,
    end: date = ANCHOR_DATE,
) -> tuple[list[DaySnapshot], dict[str, Decimal], Decimal]:
    """Walk holdings + cash backward day-by-day from ``end`` to ``start``.

    Returns (days ascending, residual_holdings_at_dawn, implied_dawn_cash).
    holdings/cash for a given day reflect the close of that day (all fills up to
    and including it). Reversing a day's fills yields the prior day's close.
    """
    qty_by_day: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    cash_by_day: dict[date, Decimal] = defaultdict(Decimal)
    for f in fills:
        qty_by_day[f.occurred_on][f.key] += f.qty_delta
        cash_by_day[f.occurred_on] += f.cash_delta
    for c in cash_events:
        cash_by_day[c.occurred_on] += c.cash_delta

    holdings: dict[str, Decimal] = defaultdict(Decimal, anchor)
    cash = cash_today
    days: list[DaySnapshot] = []
    d = end
    while d >= start:
        snap_holdings = {k: v for k, v in holdings.items() if v != 0 and not k.startswith("__")}
        days.append(DaySnapshot(d, snap_holdings, cash))
        # roll back to previous day: undo this day's fills
        for k, dv in qty_by_day.get(d, {}).items():
            holdings[k] -= dv
        cash -= cash_by_day.get(d, Decimal("0"))
        d -= timedelta(days=1)

    residual = {k: v for k, v in holdings.items() if abs(v) > _QTY_EPS and not k.startswith("__")}
    days.reverse()
    return days, residual, cash


# --- pure: pricing ------------------------------------------------------------

def price_option_day(
    meta: OptMeta,
    qty: Decimal,
    day: date,
    opt_closes: dict[str, dict[date, Decimal]],
    occ: str,
    underlying_closes: dict[str, dict[date, Decimal]],
    last_trade: dict[str, Decimal],
) -> tuple[Decimal, str]:
    """Return (per-contract value_usd for `qty` contracts, method).

    Chain: historical option close -> intrinsic (underlying close) -> carry
    (last traded price). value is signed by qty (short = negative liability).
    """
    mult = meta.multiplier or OPTION_MULTIPLIER
    hc = opt_closes.get(occ, {}).get(day)
    if hc is not None:
        return (qty * hc * mult, "historical")
    uc = underlying_closes.get(meta.underlying, {}).get(day)
    if uc is not None:
        if meta.right == "C":
            intrinsic = max(Decimal("0"), uc - meta.strike)
        else:
            intrinsic = max(Decimal("0"), meta.strike - uc)
        return (qty * intrinsic * mult, "intrinsic")
    lt = last_trade.get(occ)
    if lt is not None:
        return (qty * lt * mult, "carry")
    return (Decimal("0"), "unpriced")


def value_day(
    snap: DaySnapshot,
    equity_closes: dict[str, dict[date, Decimal]],
    opt_closes: dict[str, dict[date, Decimal]],
    occ_meta: dict[str, OptMeta],
    last_trade: dict[str, Decimal],
) -> tuple[Decimal, dict[str, int]]:
    """Compute positions value for a day; return (positions_value, method_counts)."""
    counts: dict[str, int] = defaultdict(int)
    positions_value = Decimal("0")
    for key, qty in snap.holdings.items():
        meta = occ_meta.get(key)
        if meta is None and _OCC_RE.match(key):
            meta = parse_occ(key)
        if meta is not None:  # option
            val, method = price_option_day(
                meta, qty, snap.taken_on, opt_closes, key, equity_closes, last_trade)
            positions_value += val
            counts[f"opt_{method}"] += 1
        else:  # equity
            close = equity_closes.get(key, {}).get(snap.taken_on)
            if close is not None:
                positions_value += qty * close
                counts["equity_close"] += 1
            else:
                counts["equity_missing"] += 1
    return positions_value, counts


# --- IO: raw pulls (read-only getters, cached) --------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def _load_cache(name: str):
    p = _cache_path(name)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cache(name: str, data) -> None:
    _cache_path(name).write_text(json.dumps(data, default=str))


def pull_raw(from_cache: bool) -> dict:
    """Pull complete history via read-only getters, caching each blob to /tmp."""
    import robin_stocks.robinhood as rh
    from robin_stocks.robinhood import helper as rh_helper

    from app.robinhood import rh_session

    def cached(name: str, fetch):
        if from_cache:
            data = _load_cache(name)
            if data is not None:
                print(f"  [cache] {name}: {len(data) if hasattr(data, '__len__') else 1}")
                return data
        data = fetch()
        _save_cache(name, data)
        print(f"  [pull ] {name}: {len(data) if hasattr(data, '__len__') else 1}")
        return data

    if not (from_cache and _load_cache("stock_orders") is not None):
        rh_session()  # validate session only when we actually pull

    stock_orders = cached("stock_orders", lambda: rh.get_all_stock_orders() or [])
    option_orders = cached("option_orders", lambda: rh.get_all_option_orders() or [])
    dividends = cached("dividends", lambda: rh.get_dividends() or [])
    transfers = cached("bank_transfers", lambda: rh.get_bank_transfers() or [])

    # resolve stock instrument urls -> symbols
    inst_urls = sorted({o["instrument"] for o in stock_orders if o.get("instrument")})

    def _resolve_symbols():
        out = {}
        for u in inst_urls:
            out[u] = rh.get_instrument_by_url(u, "symbol")
            time.sleep(API_SLEEP)
        return out

    symbol_by_url = cached("symbol_by_url", _resolve_symbols)

    # resolve option leg instrument urls -> metadata
    opt_urls = sorted({
        leg["option"]
        for o in option_orders for leg in (o.get("legs") or [])
        if leg.get("option")
    })

    def _resolve_opts():
        out = {}
        for u in opt_urls:
            data = rh_helper.request_get(u)  # read-only GET of the instrument url
            if not data:
                continue
            out[u] = {
                "chain_symbol": data.get("chain_symbol"),
                "expiration_date": data.get("expiration_date"),
                "strike_price": data.get("strike_price"),
                "type": data.get("type"),
            }
            time.sleep(API_SLEEP)
        return out

    opt_raw = cached("option_instruments", _resolve_opts)

    return {
        "stock_orders": stock_orders,
        "option_orders": option_orders,
        "dividends": dividends,
        "transfers": transfers,
        "symbol_by_url": symbol_by_url,
        "option_instruments": opt_raw,
    }


def pull_equity_history(symbols: list[str], from_cache: bool) -> dict[str, dict[date, Decimal]]:
    """get_stock_historicals per symbol (interval day, span 5year). Cached."""
    import robin_stocks.robinhood as rh

    out: dict[str, dict[date, Decimal]] = {}
    for sym in symbols:
        name = f"hist_stk_{sym}"
        data = _load_cache(name) if from_cache else None
        if data is None:
            data = rh.get_stock_historicals(sym, interval="day", span="5year") or []
            data = [d for d in data if d]
            _save_cache(name, data)
            print(f"  [pull ] {name}: {len(data)}")
            time.sleep(API_SLEEP)
        else:
            print(f"  [cache] {name}: {len(data)}")
        closes: dict[date, Decimal] = {}
        for pt in data:
            d = _dt_date(pt.get("begins_at"))
            c = pt.get("close_price")
            if d and c is not None:
                closes[d] = Decimal(str(c))
        out[sym] = closes
    return out


def pull_option_history(
    occ_meta: dict[str, OptMeta], from_cache: bool
) -> dict[str, dict[date, Decimal]]:
    """Best-effort option historicals (interval day, span 5year). Expired
    contracts usually return nothing -> intrinsic fallback takes over."""
    import robin_stocks.robinhood as rh

    out: dict[str, dict[date, Decimal]] = {}
    for occ, meta in occ_meta.items():
        name = f"hist_opt_{occ}"
        data = _load_cache(name) if from_cache else None
        if data is None:
            opt_type = "call" if meta.right == "C" else "put"
            try:
                data = rh.get_option_historicals(
                    meta.underlying, meta.expiry.isoformat(), str(meta.strike),
                    opt_type, interval="day", span="5year") or []
            except Exception as exc:  # expired / unresolvable
                print(f"  [warn ] {name}: {exc.__class__.__name__}")
                data = []
            data = [d for d in data if d and isinstance(d, dict)]
            _save_cache(name, data)
            print(f"  [pull ] {name}: {len(data)}")
            time.sleep(API_SLEEP)
        else:
            print(f"  [cache] {name}: {len(data)}")
        closes: dict[date, Decimal] = {}
        for pt in data:
            d = _dt_date(pt.get("begins_at"))
            c = pt.get("close_price")
            if d and c is not None:
                closes[d] = Decimal(str(c))
        if closes:
            out[occ] = closes
    return out


# --- IO: DB reads / writes ----------------------------------------------------

def read_anchor(engine: Engine):
    """Today's robinhood account:
    (external_id, holdings, cash, opt_meta, last_price, account_id)."""
    with engine.connect() as conn:
        acct = conn.execute(text(
            "SELECT id, external_id, cash_usd FROM broker_accounts "
            "WHERE broker='robinhood' ORDER BY id LIMIT 1")).one()
        rows = conn.execute(text(
            "SELECT i.symbol, i.sec_type, p.qty, i.strike, i.expiry, i.right, "
            "       i.multiplier, p.last_price_usd "
            "FROM positions p JOIN instruments i ON i.id = p.instrument_id "
            "WHERE p.broker_account_id = :a"), {"a": acct.id}).all()

    holdings: dict[str, Decimal] = {}
    opt_meta: dict[str, OptMeta] = {}
    last_price: dict[str, Decimal] = {}
    for r in rows:
        holdings[r.symbol] = Decimal(r.qty)
        if r.sec_type == "OPT":
            meta = parse_occ(r.symbol)
            if r.strike is not None:
                meta.strike = Decimal(r.strike)
            if r.expiry is not None:
                meta.expiry = r.expiry
            if r.right:
                meta.right = r.right
            if r.multiplier:
                meta.multiplier = Decimal(r.multiplier)
            opt_meta[r.symbol] = meta
        if r.last_price_usd is not None:
            last_price[r.symbol] = Decimal(r.last_price_usd)
    return acct.external_id, holdings, Decimal(acct.cash_usd), opt_meta, last_price, acct.id


def write_snapshots(engine: Engine, external_id: str, rows: list[dict]) -> int:
    """Upsert estimated snapshot rows. Guarded: only ever updates rows that are
    already fully estimated; real rows (no estimated flag) are never touched.
    Gating also guarantees taken_on < FIRST_REAL_SNAPSHOT."""
    written = 0
    with engine.begin() as conn:
        for row in rows:
            if row["taken_on"] >= FIRST_REAL_SNAPSHOT:
                continue  # hard gate — never on/after the first real snapshot
            res = conn.execute(text(
                "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
                "VALUES (:d, :total, :cash, CAST(:pa AS jsonb)) "
                "ON CONFLICT (taken_on) DO UPDATE SET "
                "  total_value_usd = EXCLUDED.total_value_usd, "
                "  cash_usd = EXCLUDED.cash_usd, "
                "  per_account = EXCLUDED.per_account "
                "WHERE (SELECT COALESCE(bool_and((v->>'estimated') = 'true'), true) "
                "       FROM jsonb_each(snapshots.per_account) AS je(k, v)) "
                "RETURNING id"),
                {"d": row["taken_on"], "total": row["total_value_usd"],
                 "cash": row["cash_usd"], "pa": json.dumps(row["per_account"])})
            if res.rowcount:
                written += 1
    return written


def write_cash_flows(engine: Engine, account_id: int, events: list[dict]) -> int:
    written = 0
    with engine.begin() as conn:
        for ev in events:
            res = conn.execute(text(
                "INSERT INTO cash_flows (broker_account_id, occurred_at, kind, "
                "                        amount_usd, currency, source_ref) "
                "VALUES (:a, :ts, :kind, :amt, 'USD', :ref) "
                "ON CONFLICT (source_ref) DO NOTHING RETURNING id"),
                {"a": account_id, "ts": ev["occurred_at"], "kind": ev["kind"],
                 "amt": ev["amount_usd"], "ref": ev["source_ref"]})
            if res.rowcount:
                written += 1
    return written


# --- orchestration ------------------------------------------------------------

def compute(engine: Engine, from_cache: bool) -> dict:
    print("Reading anchor (today's positions + cash)…")
    external_id, anchor, cash_today, anchor_opt_meta, last_price, account_id = read_anchor(engine)
    print(f"  account robinhood:{external_id}  positions={len(anchor)}  cash={cash_today}")

    print("Pulling raw history (read-only)…")
    raw = pull_raw(from_cache)

    opt_meta_by_url = {
        u: OptMeta(
            underlying=d["chain_symbol"],
            expiry=date.fromisoformat(d["expiration_date"]),
            right="C" if d["type"] == "call" else "P",
            strike=Decimal(str(d["strike_price"])),
        )
        for u, d in raw["option_instruments"].items()
        if d.get("chain_symbol") and d.get("expiration_date")
    }

    stock_fills = build_stock_fills(raw["stock_orders"], raw["symbol_by_url"])
    option_fills, occ_meta = build_option_fills(raw["option_orders"], opt_meta_by_url)
    occ_meta.update(anchor_opt_meta)  # anchor metadata wins (authoritative)
    fills = stock_fills + option_fills
    cash_events = build_cash_events(raw["dividends"], raw["transfers"])

    if not fills and not cash_events:
        raise SystemExit("no fills or cash events found — nothing to backfill")

    event_dates = [f.occurred_on for f in fills] + [c.occurred_on for c in cash_events]
    start = min(event_dates)
    print(f"  fills={len(fills)}  cash_events={len(cash_events)}  earliest={start}")

    days, residual, dawn_cash = walk_back(anchor, cash_today, fills, cash_events, start)

    # collect every symbol that appears anywhere in the timeline
    equity_symbols = {k for d in days for k in d.holdings if not _OCC_RE.match(k)}
    equity_symbols |= {m.underlying for m in occ_meta.values()}
    equity_symbols |= {parse_occ(k).underlying for d in days for k in d.holdings if _OCC_RE.match(k)}
    equity_symbols = sorted(s for s in equity_symbols if s)

    print(f"Pulling equity history for {len(equity_symbols)} symbols…")
    equity_closes = pull_equity_history(equity_symbols, from_cache)

    timeline_occ = {k for d in days for k in d.holdings if _OCC_RE.match(k)}
    price_meta = {occ: (occ_meta.get(occ) or parse_occ(occ)) for occ in timeline_occ}
    print(f"Pulling option history for {len(price_meta)} contracts (best-effort)…")
    opt_closes = pull_option_history(price_meta, from_cache)

    # value every day
    snapshot_rows: list[dict] = []
    total_counts: dict[str, int] = defaultdict(int)
    for snap in days:
        if snap.taken_on >= FIRST_REAL_SNAPSHOT:
            continue  # gate
        if not snap.holdings and snap.cash == 0:
            continue  # nothing to record
        pos_value, counts = value_day(snap, equity_closes, opt_closes, occ_meta, last_price)
        for k, v in counts.items():
            total_counts[k] += v
        pos_value = pos_value.quantize(_CENT)
        cash = snap.cash.quantize(_CENT)
        # honest cash floor: never persist negative cash, annotate below
        cash_floored = cash if cash >= 0 else Decimal("0.00")
        value = (pos_value + cash_floored).quantize(_CENT)
        per_account = {
            f"robinhood:{external_id}": {
                "positions_value_usd": str(pos_value),
                "cash_usd": str(cash_floored),
                "value_usd": str(value),
                "estimated": True,
                "pricing": dict(counts),
            }
        }
        snapshot_rows.append({
            "taken_on": snap.taken_on,
            "total_value_usd": value,
            "cash_usd": cash_floored,
            "per_account": per_account,
        })

    # cash_flows from bank transfers (dedup on transfer id)
    cash_flow_rows: list[dict] = []
    for t in raw["transfers"]:
        if (t.get("state") or "").lower() in _TRANSFER_DEAD:
            continue
        tid = t.get("id")
        d = _dt_date(t.get("created_at"))
        amt = _dec(t.get("amount"))
        if not tid or not d or amt == 0:
            continue
        direction = (t.get("direction") or "").lower()
        if direction.startswith("withdraw"):
            kind, signed = "withdrawal", -amt
        else:
            kind, signed = "deposit", amt
        cash_flow_rows.append({
            "occurred_at": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
            "kind": kind,
            "amount_usd": signed.quantize(_CENT),
            "source_ref": f"rh-transfer-{tid}",
        })

    return {
        "external_id": external_id,
        "account_id": account_id,
        "snapshot_rows": snapshot_rows,
        "cash_flow_rows": cash_flow_rows,
        "residual": residual,
        "dawn_cash": dawn_cash,
        "start": start,
        "pricing_counts": dict(total_counts),
        "anchor": anchor,
        "cash_today": cash_today,
    }


def print_summary(result: dict) -> None:
    rows = result["snapshot_rows"]
    print("\n" + "=" * 68)
    print("BACKFILL DRY-RUN SUMMARY")
    print("=" * 68)
    if not rows:
        print("No snapshot rows produced.")
        return
    first, last = rows[0], rows[-1]
    print(f"account          : robinhood:{result['external_id']}")
    print(f"date range       : {first['taken_on']} .. {last['taken_on']}  ({len(rows)} rows)")
    print(f"earliest event   : {result['start']}")
    print(f"first value_usd  : {first['total_value_usd']}  ({first['taken_on']})")
    print(f"last  value_usd  : {last['total_value_usd']}  ({last['taken_on']})")
    # landmarks
    idx = {r["taken_on"]: r for r in rows}
    for label, d in [("1y ago", ANCHOR_DATE - timedelta(days=365)),
                     ("6m ago", ANCHOR_DATE - timedelta(days=182))]:
        # nearest available on/after
        cand = [r for r in rows if r["taken_on"] >= d]
        r = cand[0] if cand else None
        if r:
            print(f"{label:16s} : {r['total_value_usd']}  ({r['taken_on']})")
    print(f"pricing methods  : {result['pricing_counts']}")
    res = {k: str(v) for k, v in result["residual"].items()}
    print(f"residual @ dawn  : {res if res else 'none (walked to ~zero ✓)'}")
    print(f"implied dawn cash: {result['dawn_cash'].quantize(_CENT)}")
    print(f"cash_flow rows   : {len(result['cash_flow_rows'])}")
    # sanity annotations
    anchor_total = None
    if last["per_account"]:
        anchor_total = last["total_value_usd"]
    if result["dawn_cash"] < 0:
        print("  ! implied dawn cash is NEGATIVE — floored at 0 in written rows "
              "(uncaptured interest/fees/reinvest drift).")
    if result["residual"] and anchor_total:
        # crude residual-vs-portfolio flag
        print("  ! residual holdings present at dawn — likely shares transferred-in "
              "or missing order history. Review before executing.")
    print("=" * 68 + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Portfolio snapshot backfill (estimated, pre-go-live).")
    ap.add_argument("--dry-run", action="store_true", help="compute + summary, write nothing")
    ap.add_argument("--execute", action="store_true", help="write snapshots + cash_flows")
    ap.add_argument("--from-cache", action="store_true", help="reuse /tmp raw pulls (no network)")
    args = ap.parse_args()

    if not (args.dry_run or args.execute):
        ap.error("pass --dry-run or --execute")
    if args.dry_run and args.execute:
        ap.error("--dry-run and --execute are mutually exclusive")

    engine = create_engine(settings.database_url)
    result = compute(engine, args.from_cache)
    print_summary(result)

    if args.dry_run:
        print("DRY-RUN: nothing written.")
        return 0

    print("EXECUTE: writing snapshots + cash_flows…")
    n_snap = write_snapshots(engine, result["external_id"], result["snapshot_rows"])
    n_flow = write_cash_flows(engine, result["account_id"], result["cash_flow_rows"])
    print(f"  wrote/updated {n_snap} snapshot rows, {n_flow} cash_flow rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
