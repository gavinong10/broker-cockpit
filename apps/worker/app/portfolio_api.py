"""Portfolio read API: unified positions, per-symbol detail, snapshot history.

All routes are internal-auth and return JSON with Decimals serialized as
strings. Positions are aggregated across brokers per instrument, with a
per-broker breakdown. Staleness thresholds are imported from app.scheduler
(the writer side) — never re-derived here.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.internal_auth import require_internal
from app.scheduler import is_stale

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])

_CENT = Decimal("0.01")
_PCT = Decimal("0.0001")
_ZERO = Decimal("0")

SNAPSHOT_DAYS_DEFAULT = 90
SNAPSHOT_DAYS_CAP = 3650

_POSITION_ROWS_SQL = (
    "SELECT i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", "
    "COALESCE(i.multiplier, 1) AS multiplier, "
    "a.broker, a.external_id, "
    "p.qty, p.avg_cost_usd, p.last_price_usd, p.prev_close_usd "
    "FROM positions p "
    "JOIN instruments i ON i.id = p.instrument_id "
    "JOIN broker_accounts a ON a.id = p.broker_account_id ")

# basket chips: open-basket allocations keyed by the same instrument tuple
# that _aggregate groups on (symbol, sec_type, expiry, strike, right)
_BASKET_CHIP_SQL = (
    "SELECT i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", "
    "b.slug, SUM(ba.qty) AS qty "
    "FROM basket_allocations ba "
    "JOIN baskets b ON b.id = ba.basket_id AND b.status = 'open' "
    "JOIN instruments i ON i.id = ba.instrument_id "
    "GROUP BY i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", b.slug "
    "ORDER BY b.slug")


def _get_engine():
    from app import main  # deferred: main imports this module at startup
    return main.get_engine()


def _s(value):
    """Decimal/other -> string, passing None through (JSON null)."""
    return None if value is None else str(value)


def _row_market_value(row) -> Decimal:
    if row.last_price_usd is None:
        return _ZERO
    return row.qty * row.last_price_usd * row.multiplier


def _row_day_change(row) -> Decimal:
    if row.last_price_usd is None or row.prev_close_usd is None:
        return _ZERO
    return (row.last_price_usd - row.prev_close_usd) * row.qty * row.multiplier


def _row_unrealized(row) -> Decimal:
    if row.last_price_usd is None or row.avg_cost_usd is None:
        return _ZERO
    return (row.last_price_usd - row.avg_cost_usd) * row.qty * row.multiplier


def _aggregate(rows) -> list[dict]:
    """Group per-broker position rows per instrument; values stay Decimal."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row.symbol, row.sec_type, row.expiry, row.strike, row.right)
        g = groups.setdefault(key, {
            "symbol": row.symbol,
            "sec_type": row.sec_type,
            "expiry": row.expiry,
            "strike": row.strike,
            "right": row.right,
            "qty": _ZERO,
            "cost_notional": _ZERO,      # sum(qty * avg_cost), unit-price basis
            "cost_qty": _ZERO,           # qty covered by a known avg_cost
            "last_price_usd": None,
            "prev_close_usd": None,
            "market_value_usd": _ZERO,
            "day_change_usd": _ZERO,
            "unrealized_pl_usd": _ZERO,
            "broker_qty": {},
        })
        g["qty"] += row.qty
        if row.avg_cost_usd is not None:
            g["cost_notional"] += row.qty * row.avg_cost_usd
            g["cost_qty"] += row.qty
        if g["last_price_usd"] is None:
            g["last_price_usd"] = row.last_price_usd
        if g["prev_close_usd"] is None:
            g["prev_close_usd"] = row.prev_close_usd
        g["market_value_usd"] += _row_market_value(row)
        g["day_change_usd"] += _row_day_change(row)
        g["unrealized_pl_usd"] += _row_unrealized(row)
        g["broker_qty"][row.broker] = g["broker_qty"].get(row.broker, _ZERO) + row.qty

    aggs = []
    for g in groups.values():
        g["avg_cost_usd"] = (
            g["cost_notional"] / g["cost_qty"] if g["cost_qty"] != 0 else None)
        aggs.append(g)
    aggs.sort(key=lambda g: g["market_value_usd"], reverse=True)
    return aggs


def _basket_chips(rows) -> dict[tuple, list[dict]]:
    chips: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row.symbol, row.sec_type, row.expiry, row.strike, row.right)
        chips.setdefault(key, []).append({"slug": row.slug, "qty": str(row.qty)})
    return chips


def _serialize_position(g: dict, total_value: Decimal,
                        baskets: list[dict] | None = None) -> dict:
    weight = (g["market_value_usd"] / total_value * 100).quantize(_PCT) \
        if total_value != 0 else _ZERO
    return {
        "symbol": g["symbol"],
        "sec_type": g["sec_type"],
        "qty": str(g["qty"]),
        "avg_cost_usd": _s(g["avg_cost_usd"]),
        "last_price_usd": _s(g["last_price_usd"]),
        "prev_close_usd": _s(g["prev_close_usd"]),
        "market_value_usd": str(g["market_value_usd"].quantize(_CENT)),
        "unrealized_pl_usd": str(g["unrealized_pl_usd"].quantize(_CENT)),
        "day_change_usd": str(g["day_change_usd"].quantize(_CENT)),
        "weight_pct": str(weight),
        "expiry": g["expiry"].isoformat() if g["expiry"] else None,
        "strike": _s(g["strike"]),
        "right": g["right"],
        "brokers": [{"broker": b, "qty": str(q)}
                    for b, q in sorted(g["broker_qty"].items())],
        "baskets": baskets or [],   # empty = core (unallocated)
    }


@router.get("/portfolio")
def portfolio():
    now = datetime.now(timezone.utc)
    with _get_engine().connect() as conn:
        acct_rows = conn.execute(text(
            "SELECT broker, external_id, cash_usd, last_synced_at "
            "FROM broker_accounts ORDER BY broker, external_id")).all()
        pos_rows = conn.execute(text(_POSITION_ROWS_SQL)).all()
        chip_rows = conn.execute(text(_BASKET_CHIP_SQL)).all()
    chips = _basket_chips(chip_rows)

    aggs = _aggregate(pos_rows)
    cash_total = sum((a.cash_usd for a in acct_rows), _ZERO)
    total = cash_total + sum((g["market_value_usd"] for g in aggs), _ZERO)
    day_change = sum((g["day_change_usd"] for g in aggs), _ZERO)
    denominator = total - day_change
    day_change_pct = (day_change / denominator * 100).quantize(_PCT) \
        if denominator != 0 else _ZERO

    return {
        "total_value_usd": str(total.quantize(_CENT)),
        "day_change_usd": str(day_change.quantize(_CENT)),
        "day_change_pct": str(day_change_pct),
        "cash_usd": str(cash_total.quantize(_CENT)),
        "accounts": [{
            "broker": a.broker,
            "external_id": a.external_id,
            "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
            "stale": is_stale(a.last_synced_at, now),
        } for a in acct_rows],
        "positions": [_serialize_position(
            g, total,
            chips.get((g["symbol"], g["sec_type"], g["expiry"], g["strike"], g["right"])))
            for g in aggs],
    }


@router.get("/positions/{symbol}")
def position_detail(symbol: str):
    with _get_engine().connect() as conn:
        rows = conn.execute(
            text(_POSITION_ROWS_SQL + "WHERE i.symbol = :symbol"),
            {"symbol": symbol}).all()
    if not rows:
        raise HTTPException(status_code=404, detail="unknown symbol")

    g = _aggregate(rows)[0]
    return {
        "symbol": g["symbol"],
        "sec_type": g["sec_type"],
        "qty": str(g["qty"]),
        "avg_cost_usd": _s(g["avg_cost_usd"]),
        "last_price_usd": _s(g["last_price_usd"]),
        "prev_close_usd": _s(g["prev_close_usd"]),
        "market_value_usd": str(g["market_value_usd"].quantize(_CENT)),
        "unrealized_pl_usd": str(g["unrealized_pl_usd"].quantize(_CENT)),
        "day_change_usd": str(g["day_change_usd"].quantize(_CENT)),
        "expiry": g["expiry"].isoformat() if g["expiry"] else None,
        "strike": _s(g["strike"]),
        "right": g["right"],
        "accounts": [{
            "broker": r.broker,
            "external_id": r.external_id,
            "qty": str(r.qty),
            "avg_cost_usd": _s(r.avg_cost_usd),
            "market_value_usd": str(_row_market_value(r).quantize(_CENT)),
            "unrealized_pl_usd": str(_row_unrealized(r).quantize(_CENT)),
        } for r in rows],
    }


@router.get("/snapshots")
def snapshots(days: int = Query(default=SNAPSHOT_DAYS_DEFAULT, ge=1)):
    days = min(days, SNAPSHOT_DAYS_CAP)
    with _get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT taken_on, total_value_usd FROM snapshots "
            "WHERE taken_on >= CURRENT_DATE - CAST(:days AS integer) "
            "ORDER BY taken_on ASC"), {"days": days}).all()
    return [{"taken_on": r.taken_on.isoformat(),
             "total_value_usd": str(r.total_value_usd)} for r in rows]
