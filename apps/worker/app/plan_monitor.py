"""Plan monitor: grade pending plan legs against live quotes each sync cycle.

For every pending/partial plan leg of an open basket, fetch a live net cost
for the planned structure (Robinhood market data, read-only), classify entry
conditions vs. the plan, record a basket_plan_marks row, and alert (Discord)
on status *transitions* only.

Statuses (plan doc 2026-07-11-basket-plan-monitor.md):
  in_window     live net cost <= planned_net_debit x (1 + tolerance_pct/100)
  drifted       costlier than tolerance allows
  thesis_stale  debit vertical whose live cost >= 80% of its width — the entry
                no longer resembles the plan (payoff multiple < 1.25x), usually
                because the underlying ran through the strikes. Only computed
                for two-leg same-underlying verticals; other structures never
                go stale by this rule.
  unquotable    at least one contract had no usable quote this cycle

Quote basis per contract: bid/ask mid when both sides exist, else the
adjusted/regular mark, else last trade. A structure's reported basis is the
weakest basis among its contracts (mid > mark > last).

This module is read-only with respect to trading; it never places orders.
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.notify import alert
from app.robinhood import rh_session

log = logging.getLogger(__name__)

_OCC_PARSE_RE = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")

ALERTABLE = ("in_window", "drifted", "thesis_stale")
_BASIS_RANK = {"mid": 0, "mark": 1, "last": 2}   # higher = weaker


@dataclass(frozen=True)
class StructureQuote:
    net_cost: Decimal | None      # per share; None = unquotable
    basis: str | None             # weakest contract basis: mid | mark | last
    underlying_spot: Decimal | None


def parse_occ(occ: str) -> tuple[str, date, str, Decimal]:
    """Inverse of robinhood.occ_symbol: SYM + YYMMDD + C/P + strike*1000 (8d)."""
    m = _OCC_PARSE_RE.match(occ)
    if not m:
        raise ValueError(f"not an OCC symbol: {occ!r}")
    sym, yy, mm, dd, right, strike_milli = m.groups()
    return (sym, date(2000 + int(yy), int(mm), int(dd)), right,
            Decimal(strike_milli) / 1000)


def structure_width(structure: list[dict]) -> Decimal | None:
    """Payoff cap per share for a two-leg debit vertical; None for other shapes."""
    if len(structure) != 2:
        return None
    try:
        parsed = [(parse_occ(c["occ"]), c["ratio"]) for c in structure
                  if c.get("sec_type") == "OPT"]
    except (ValueError, KeyError):
        return None
    if len(parsed) != 2:
        return None
    (u1, e1, r1, k1), ratio1 = parsed[0]
    (u2, e2, r2, k2), ratio2 = parsed[1]
    if u1 != u2 or e1 != e2 or r1 != r2 or sorted((ratio1, ratio2)) != [-1, 1]:
        return None
    return abs(k1 - k2)


def _dec(value) -> Decimal | None:
    """Decimal or None; zero counts as absent (RH reports missing sides as 0)."""
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d != 0 else None


def _contract_price(md: dict) -> tuple[Decimal, str] | None:
    bid, ask = _dec(md.get("bid_price")), _dec(md.get("ask_price"))
    if bid is not None and ask is not None:
        return ((bid + ask) / 2, "mid")
    mark = _dec(md.get("adjusted_mark_price")) or _dec(md.get("mark_price"))
    if mark is not None:
        return (mark, "mark")
    last = _dec(md.get("last_trade_price"))
    if last is not None:
        return (last, "last")
    return None


def fetch_structure_quote(structure: list[dict]) -> StructureQuote:
    """Live net cost for one structure via robin_stocks (read-only calls)."""
    import robin_stocks.robinhood as rh

    net = Decimal("0")
    weakest = "mid"
    spot_symbol: str | None = None
    for contract in structure:
        ratio = Decimal(contract["ratio"])
        if contract["sec_type"] == "OPT":
            underlying, expiry, right, strike = parse_occ(contract["occ"])
            spot_symbol = spot_symbol or underlying
            data = rh.get_option_market_data(
                underlying, expiry.isoformat(), str(strike.normalize()),
                "call" if right == "C" else "put") or []
            flat = data[0] if data and isinstance(data[0], list) else data
            md = flat[0] if flat else None
            priced = _contract_price(md) if md else None
        else:  # STK
            symbol = contract["symbol"]
            spot_symbol = spot_symbol or symbol
            raw = (rh.get_latest_price(symbol) or [None])[0]
            price = _dec(raw)
            priced = (price, "last") if price is not None else None
        if priced is None:
            return StructureQuote(None, None, _spot(spot_symbol))
        price, basis = priced
        if _BASIS_RANK[basis] > _BASIS_RANK[weakest]:
            weakest = basis
        net += ratio * price
    return StructureQuote(net, weakest, _spot(spot_symbol))


def _spot(symbol: str | None) -> Decimal | None:
    if symbol is None:
        return None
    import robin_stocks.robinhood as rh
    try:
        return _dec((rh.get_latest_price(symbol) or [None])[0])
    except Exception:
        return None


def classify(planned_net_debit: Decimal, tolerance_pct: Decimal,
             net_cost: Decimal | None, width: Decimal | None) -> str:
    if net_cost is None:
        return "unquotable"
    if width is not None and net_cost >= width * Decimal("0.8"):
        return "thesis_stale"
    if net_cost <= planned_net_debit * (1 + tolerance_pct / 100):
        return "in_window"
    return "drifted"


def _alert_text(slug: str, leg_label: str, status: str, planned: Decimal,
                net: Decimal | None, spot: Decimal | None, basis: str | None) -> tuple[str, str]:
    delta_pct = (f"{(net / planned - 1) * 100:+.1f}%"
                 if (net is not None and planned) else "n/a")
    lines = [f"Basket `{slug}` — {leg_label}",
             f"planned {planned} vs live {net if net is not None else '?'} ({delta_pct}, {basis or 'no quote'})"]
    if spot is not None:
        lines.append(f"underlying spot {spot}")
    titles = {
        "in_window": f"🟢 Entry window open: {leg_label}",
        "drifted": f"🟡 Plan drifted: {leg_label}",
        "thesis_stale": f"🔴 Plan thesis stale: {leg_label}",
    }
    return titles[status], "\n".join(lines)


def monitor_plans(engine: Engine, quote_fn=None) -> dict:
    """One monitoring pass over all pending/partial legs of open baskets.

    quote_fn(structure) -> StructureQuote is injectable for tests; the default
    restores the RH session once and uses live market data.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT pl.id, pl.label, pl.structure, pl.planned_net_debit, "
            "pl.tolerance_pct, pl.monitor_status, pl.last_alerted_status, b.slug "
            "FROM basket_plan_legs pl JOIN baskets b ON b.id = pl.basket_id "
            "WHERE pl.status IN ('pending', 'partial') AND b.status = 'open' "
            "ORDER BY pl.id")).all()
    if not rows:
        return {"checked": 0, "alerted": 0}

    if quote_fn is None:
        rh_session()
        quote_fn = fetch_structure_quote

    alerted = 0
    statuses: dict[str, int] = {}
    for row in rows:
        structure = row.structure if isinstance(row.structure, list) else json.loads(row.structure)
        try:
            quote = quote_fn(structure)
        except Exception as exc:  # a bad leg must not sink the whole pass
            log.warning("plan quote failed for %s/%s: %s", row.slug, row.label, exc)
            quote = StructureQuote(None, None, None)
        status = classify(Decimal(row.planned_net_debit), Decimal(row.tolerance_pct),
                          quote.net_cost, structure_width(structure))
        statuses[status] = statuses.get(status, 0) + 1

        should_alert = (status in ALERTABLE and status != row.last_alerted_status)
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE basket_plan_legs SET monitor_status = :st, "
                "last_quote_net = :net, last_quoted_at = now()"
                + (", last_alerted_status = :st" if should_alert else "")
                + " WHERE id = :id"),
                {"st": status, "net": quote.net_cost, "id": row.id})
            conn.execute(text(
                "INSERT INTO basket_plan_marks (plan_leg_id, net_cost, "
                "underlying_spot, quote_basis) VALUES (:l, :n, :s, :b)"),
                {"l": row.id, "n": quote.net_cost, "s": quote.underlying_spot,
                 "b": quote.basis})
        if should_alert:
            title, desc = _alert_text(row.slug, row.label, status,
                                      Decimal(row.planned_net_debit),
                                      quote.net_cost, quote.underlying_spot,
                                      quote.basis)
            alert(title, desc)
            alerted += 1

    return {"checked": len(rows), "alerted": alerted, "statuses": statuses}
