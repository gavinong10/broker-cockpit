"""Portfolio read API: unified positions, per-symbol detail, snapshot history.

All routes are internal-auth and return JSON with Decimals serialized as
strings. Positions are aggregated across brokers per instrument, with a
per-broker breakdown. Staleness thresholds are imported from app.scheduler
(the writer side) — never re-derived here.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app import performance
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


def _tags_map(conn) -> dict[str, list]:
    """underlying -> theme tags. Options inherit their underlying's tags."""
    return {r.underlying: r.tags for r in conn.execute(
        text("SELECT underlying, tags FROM underlying_tags")).all()}


def _tags_for(g: dict, tags: dict[str, list]) -> list:
    from app.baskets import parse_underlying
    underlying = parse_underlying(g["symbol"]) if g["sec_type"] == "OPT" else g["symbol"]
    return tags.get(underlying, [])


def _serialize_position(g: dict, total_value: Decimal,
                        baskets: list[dict] | None = None,
                        tags: list | None = None) -> dict:
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
        "tags": tags or [],         # theme tags inherited from the underlying
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
        tags = _tags_map(conn)
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
            chips.get((g["symbol"], g["sec_type"], g["expiry"], g["strike"], g["right"])),
            _tags_for(g, tags))
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
            "SELECT taken_on, total_value_usd, source FROM snapshots "
            "WHERE taken_on >= CURRENT_DATE - CAST(:days AS integer) "
            "ORDER BY taken_on ASC"), {"days": days}).all()
    return [{"taken_on": r.taken_on.isoformat(),
             "total_value_usd": str(r.total_value_usd),
             "source": r.source} for r in rows]


@router.get("/cashflows")
def cashflows(days: int = Query(default=SNAPSHOT_DAYS_DEFAULT, ge=1)):
    """Per-day net external flows (deposits +, withdrawals -), for the
    deposits-baseline overlay on the value chart."""
    days = min(days, SNAPSHOT_DAYS_CAP)
    with _get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT CAST(occurred_at AS date) AS occurred_on, "
            "SUM(amount_usd) AS net_usd FROM cash_flows "
            "WHERE occurred_at >= CURRENT_DATE - CAST(:days AS integer) "
            "GROUP BY CAST(occurred_at AS date) ORDER BY occurred_on ASC"),
            {"days": days}).all()
    return [{"occurred_on": r.occurred_on.isoformat(),
             "net_usd": str(r.net_usd)} for r in rows]


@router.post("/snapshots/backfill")
def snapshots_backfill(span: str = Query(default="year")):
    from app.value_history import backfill_snapshots
    if span not in ("week", "month", "3month", "year", "5year"):
        raise HTTPException(status_code=400, detail="bad span")
    return backfill_snapshots(_get_engine(), span=span)


@router.post("/cashflows/sync")
def cashflows_sync():
    from app.value_history import sync_cash_flows
    return sync_cash_flows(_get_engine())


# --- flow-adjusted performance (design spec §4.1) -----------------------------

_PERIODS = ("inception", "1y", "ytd", "all")


def _snapshot_estimated(source: str | None, per_account) -> bool:
    """A snapshot is estimated if it's a backfill row, or any per-account entry
    carries the estimated flag written by scripts/backfill_snapshots.py."""
    if source and source != "observed":
        return True
    if isinstance(per_account, dict):
        return any(isinstance(v, dict) and v.get("estimated") is True
                   for v in per_account.values())
    return False


def _pct_str(rate: float | None):
    """Annualized decimal rate -> percent string (2 dp), or None."""
    return None if rate is None else str(round(rate * 100, 2))


def _period_start(period: str, end_date: date) -> date | None:
    if period == "1y":
        return end_date - timedelta(days=365)
    if period == "ytd":
        return date(end_date.year, 1, 1)
    return None  # inception / all -> account life, no boundary


@router.get("/performance")
def performance_endpoint(period: str = Query(default="inception")):
    """Flow-adjusted account performance for the given period.

    Since-inception dollar P&L and money-weighted return use ONLY dated external
    flows + today's exact (observed) value -> solid:true. The time-weighted
    return and any sub-period boundary lean on the reconstructed (estimated)
    daily series -> flagged in caveats (and solid:false for sub-periods).
    """
    if period not in _PERIODS:
        raise HTTPException(status_code=400, detail="bad period")

    with _get_engine().connect() as conn:
        flow_rows = conn.execute(text(
            "SELECT CAST(occurred_at AS date) AS d, amount_usd "
            "FROM cash_flows ORDER BY occurred_at, id")).all()
        snap_rows = conn.execute(text(
            "SELECT taken_on, total_value_usd, source, per_account "
            "FROM snapshots ORDER BY taken_on ASC")).all()

    if not snap_rows:
        raise HTTPException(status_code=409, detail="no snapshots yet")

    # Investor-convention flows (deposit negative, withdrawal positive): the DB
    # stores +deposit / -withdrawal, so a single negation crosses the boundary.
    all_flows: list[tuple[date, Decimal]] = [
        (r.d, -Decimal(r.amount_usd)) for r in flow_rows]

    # Terminal = the latest (observed) snapshot: today's exact value.
    terminal = snap_rows[-1]
    end_date = terminal.taken_on
    current_value = Decimal(terminal.total_value_usd)

    value_series = [{
        "date": r.taken_on.isoformat(),
        "value_usd": str(Decimal(r.total_value_usd)),
        "estimated": _snapshot_estimated(r.source, r.per_account),
    } for r in snap_rows]
    contributions_series = [
        {"date": d.isoformat(), "value_usd": str(v)}
        for d, v in performance.net_contributions_series(all_flows)]

    caveats = [
        "Daily value before go-live (2026-07-11) is reconstructed from Robinhood "
        "records and is estimated; the time-weighted return derives from it and "
        "is therefore estimated."
    ]

    start = _period_start(period, end_date)
    daily_pairs = [(r.taken_on, Decimal(r.total_value_usd)) for r in snap_rows]

    if start is None:
        # Inception / all: real flows only + today's exact value. Nothing rests
        # on an estimated boundary -> solid.
        period_real_flows = all_flows
        pnl_flows = all_flows
        twr_daily = daily_pairs
        solid = True
        caveats.append(
            "Dollar P&L and money-weighted return use only dated cash flows and "
            "today's exact value.")
    else:
        # Sub-period: value at the period boundary seeds a synthetic 'deposit',
        # sourced from a snapshot (estimated pre go-live) -> solid:false.
        boundary = None
        for r in snap_rows:
            if r.taken_on <= start:
                boundary = r
            else:
                break
        period_real_flows = [f for f in all_flows
                             if (boundary.taken_on if boundary else start) < f[0] <= end_date]
        if boundary is not None:
            v0 = Decimal(boundary.total_value_usd)
            pnl_flows = [(boundary.taken_on, -v0)] + period_real_flows
            twr_daily = [p for p in daily_pairs if p[0] >= boundary.taken_on]
            if _snapshot_estimated(boundary.source, boundary.per_account):
                caveats.append(
                    f"Period opening value ({boundary.taken_on.isoformat()}) is an "
                    "estimated pre-go-live snapshot.")
        else:
            # Period predates all snapshots -> behaves like inception within data.
            pnl_flows = period_real_flows
            twr_daily = daily_pairs
        solid = False

    mwr = performance.money_weighted_return(pnl_flows, current_value, end_date)
    tw = performance.twr(twr_daily, period_real_flows)

    return {
        "period": period,
        "mwr_pct": _pct_str(mwr),
        "twr_pct": _pct_str(tw),
        "dollar_pnl_usd": str(performance.dollar_pnl(pnl_flows, current_value).quantize(_CENT)),
        "net_contributions_usd": str(
            performance.net_contributions(period_real_flows).quantize(_CENT)),
        "current_value_usd": str(current_value.quantize(_CENT)),
        "value_series": value_series,
        "contributions_series": contributions_series,
        "solid": solid,
        "caveats": caveats,
    }


@router.get("/exposure")
def exposure():
    """Dollar exposure per underlying: stock market value + option market value.

    Options count at their market value (premium marks, signed — short options
    subtract), grouped under the OCC symbol's underlying so 'exposure to SLS'
    includes SLS shares and every SLS option line.
    """
    from app.baskets import parse_underlying

    with _get_engine().connect() as conn:
        rows = conn.execute(text(_POSITION_ROWS_SQL)).all()
        chip_rows = conn.execute(text(_BASKET_CHIP_SQL)).all()
        tags = _tags_map(conn)
    # OCC symbols are unique per contract, so (symbol, sec_type) is a safe key.
    chips: dict[tuple, list[dict]] = {}
    for c in chip_rows:
        chips.setdefault((c.symbol, c.sec_type), []).append(
            {"slug": c.slug, "qty": str(c.qty)})
    groups: dict[str, dict] = {}
    for r in rows:
        underlying = parse_underlying(r.symbol) if r.sec_type == "OPT" else r.symbol
        g = groups.setdefault(underlying, {"stock": _ZERO, "options": _ZERO, "cons": {}})
        mv = _row_market_value(r)
        g["options" if r.sec_type == "OPT" else "stock"] += mv
        # Constituents aggregate across broker accounts, like the portfolio view.
        key = (r.symbol, r.sec_type)
        con = g["cons"].setdefault(key, {
            "symbol": r.symbol,
            "sec_type": r.sec_type,
            "expiry": r.expiry.isoformat() if r.expiry else None,
            "strike": str(r.strike) if r.strike is not None else None,
            "right": r.right,
            "qty": _ZERO,
            "mv": _ZERO,
        })
        con["qty"] += r.qty
        con["mv"] += mv
    total_abs = sum((abs(g["stock"] + g["options"]) for g in groups.values()), _ZERO)
    out = []
    for underlying, g in groups.items():
        net = g["stock"] + g["options"]
        positions = sorted(g["cons"].values(), key=lambda c: abs(c["mv"]), reverse=True)
        out.append({
            "underlying": underlying,
            "tags": tags.get(underlying, []),
            "stock_value_usd": str(g["stock"].quantize(_CENT)),
            "option_value_usd": str(g["options"].quantize(_CENT)),
            "total_usd": str(net.quantize(_CENT)),
            "weight_pct": str(((abs(net) / total_abs * 100).quantize(_PCT))
                              if total_abs else _ZERO),
            "positions": [{
                "symbol": c["symbol"],
                "sec_type": c["sec_type"],
                "expiry": c["expiry"],
                "strike": c["strike"],
                "right": c["right"],
                "qty": str(c["qty"]),
                "market_value_usd": str(c["mv"].quantize(_CENT)),
                "baskets": chips.get((c["symbol"], c["sec_type"]), []),
            } for c in positions],
        })
    out.sort(key=lambda e: abs(Decimal(e["total_usd"])), reverse=True)
    return out
