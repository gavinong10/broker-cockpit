"""Unit tests for the portfolio backfill walk-back + pricing + gating logic.

Pure-function tests (no DB, no network) run everywhere; the postgres-gated
write test exercises the snapshot upsert guard against a real row.
"""
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from scripts.backfill_snapshots import (
    ANCHOR_DATE,
    FIRST_REAL_SNAPSHOT,
    CashEvent,
    Fill,
    OptMeta,
    build_cash_events,
    build_option_fills,
    build_stock_fills,
    parse_occ,
    price_option_day,
    value_day,
    walk_back,
    write_snapshots,
    DaySnapshot,
    _occ,
)

D = Decimal


# --- OCC round-trip -----------------------------------------------------------

def test_parse_occ_roundtrip():
    occ = _occ("NBIS", date(2028, 12, 15), "C", D("220"))
    assert occ == "NBIS281215C00220000"
    meta = parse_occ(occ)
    assert meta.underlying == "NBIS"
    assert meta.expiry == date(2028, 12, 15)
    assert meta.right == "C"
    assert meta.strike == D("220")


# --- walk-back: equities, incl fractional, reversal to zero -------------------

def _fill(d, key, qty, cash, sec="STK"):
    return Fill(d, key, sec, D(qty), D(cash))


def test_walk_back_buy_sell_reverses_to_zero():
    # buy 10 @ 100 on day1, sell 4 @ 110 on day2. Anchor (today) holds 6.
    anchor = {"AAA": D("6")}
    cash_today = D("560")  # -1000 +440 +1120 start... value irrelevant to holdings
    fills = [
        _fill(date(2026, 1, 5), "AAA", "10", "-1000"),
        _fill(date(2026, 1, 8), "AAA", "-4", "440"),
    ]
    days, residual, dawn_cash = walk_back(
        anchor, cash_today, fills, [], start=date(2026, 1, 5))
    by_day = {s.taken_on: s for s in days}
    # on Jan 5 (after buy): 10 shares
    assert by_day[date(2026, 1, 5)].holdings == {"AAA": D("10")}
    # on Jan 8 (after sell): 6 shares
    assert by_day[date(2026, 1, 8)].holdings == {"AAA": D("6")}
    # anchor day holds 6
    assert by_day[ANCHOR_DATE].holdings == {"AAA": D("6")}
    # walked all the way back -> zero residual
    assert residual == {}


def test_walk_back_fractional_shares():
    anchor = {"BBB": D("2.5")}
    fills = [
        _fill(date(2026, 2, 1), "BBB", "1.5", "-150"),
        _fill(date(2026, 2, 3), "BBB", "1.0", "-100"),
    ]
    days, residual, _ = walk_back(anchor, D("0"), fills, [], start=date(2026, 2, 1))
    by_day = {s.taken_on: s for s in days}
    assert by_day[date(2026, 2, 1)].holdings == {"BBB": D("1.5")}
    assert by_day[date(2026, 2, 3)].holdings == {"BBB": D("2.5")}
    assert residual == {}


def test_walk_back_residual_when_history_missing():
    # anchor holds 10 but only a buy of 7 is known -> 3 shares transferred in
    anchor = {"CCC": D("10")}
    fills = [_fill(date(2026, 3, 1), "CCC", "7", "-700")]
    days, residual, _ = walk_back(anchor, D("0"), fills, [], start=date(2026, 3, 1))
    assert residual == {"CCC": D("3")}


# --- walk-back: short option open then close ----------------------------------

def test_walk_back_short_option_open_close():
    occ = "SLS260821P00090000"
    # open short (-1) on day1 (credit +210), close (buy +1) on day2 (debit -80).
    # Anchor holds 0 (position closed) -> not in anchor.
    anchor: dict = {}
    fills = [
        Fill(date(2026, 6, 1), occ, "OPT", D("-1"), D("210")),
        Fill(date(2026, 6, 5), occ, "OPT", D("1"), D("-80")),
    ]
    days, residual, _ = walk_back(anchor, D("0"), fills, [], start=date(2026, 6, 1))
    by_day = {s.taken_on: s for s in days}
    assert by_day[date(2026, 6, 1)].holdings == {occ: D("-1")}  # short open
    assert by_day[date(2026, 6, 5)].holdings == {}              # closed -> flat
    assert residual == {}


# --- cash walk-back -----------------------------------------------------------

def test_walk_back_cash_with_dividends_and_transfers():
    anchor: dict = {}
    cash_today = D("1000")
    # deposit +900 on day1, dividend +50 on day2, buy -200 (fill) on day3
    fills = [_fill(date(2026, 4, 3), "ZZZ", "2", "-200")]
    cash_events = [
        CashEvent(date(2026, 4, 1), D("900"), "deposit"),
        CashEvent(date(2026, 4, 2), D("50"), "dividend"),
    ]
    days, _, dawn_cash = walk_back(
        anchor, cash_today, fills, cash_events, start=date(2026, 4, 1))
    by_day = {s.taken_on: s.cash for s in days}
    assert by_day[ANCHOR_DATE] == D("1000")
    assert by_day[date(2026, 4, 3)] == D("1000")   # after the -200 buy
    assert by_day[date(2026, 4, 2)] == D("1200")   # before buy, after dividend
    assert by_day[date(2026, 4, 1)] == D("1150")   # before dividend, after deposit
    # dawn (before deposit): 1150 - 900 = 250 residual drift
    assert dawn_cash == D("250")


# --- pricing fallback chain ---------------------------------------------------

def test_price_option_historical_wins():
    meta = OptMeta("SLS", date(2026, 8, 21), "P", D("90"))
    occ = "SLS260821P00090000"
    opt_closes = {occ: {date(2026, 6, 1): D("2.10")}}
    val, method = price_option_day(
        meta, D("-1"), date(2026, 6, 1), opt_closes, occ, {}, {})
    assert method == "historical"
    assert val == D("-1") * D("2.10") * D("100")  # short => negative liability


def test_price_option_intrinsic_fallback_call():
    meta = OptMeta("AAA", date(2026, 8, 21), "C", D("100"))
    occ = "AAA260821C00100000"
    underlying = {"AAA": {date(2026, 6, 1): D("120")}}
    val, method = price_option_day(
        meta, D("2"), date(2026, 6, 1), {}, occ, underlying, {})
    assert method == "intrinsic"
    assert val == D("2") * (D("120") - D("100")) * D("100")  # 4000


def test_price_option_intrinsic_put_zero_floor():
    meta = OptMeta("AAA", date(2026, 8, 21), "P", D("90"))
    occ = "AAA260821P00090000"
    underlying = {"AAA": {date(2026, 6, 1): D("120")}}  # OTM put -> intrinsic 0
    val, method = price_option_day(
        meta, D("1"), date(2026, 6, 1), {}, occ, underlying, {})
    assert method == "intrinsic"
    assert val == D("0")


def test_price_option_carry_last_fallback():
    meta = OptMeta("AAA", date(2026, 8, 21), "C", D("100"))
    occ = "AAA260821C00100000"
    last = {occ: D("3.33")}
    val, method = price_option_day(
        meta, D("1"), date(2026, 6, 1), {}, occ, {}, last)
    assert method == "carry"
    assert val == D("3.33") * D("100")


def test_price_option_unpriced():
    meta = OptMeta("AAA", date(2026, 8, 21), "C", D("100"))
    val, method = price_option_day(
        meta, D("1"), date(2026, 6, 1), {}, "AAA260821C00100000", {}, {})
    assert method == "unpriced"
    assert val == D("0")


def test_value_day_mixes_equity_and_option():
    occ = "AAA260821C00100000"
    snap = DaySnapshot(date(2026, 6, 1), {"AAA": D("10"), occ: D("2")}, D("500"))
    equity = {"AAA": {date(2026, 6, 1): D("120")}}
    occ_meta = {occ: OptMeta("AAA", date(2026, 8, 21), "C", D("100"))}
    pos_value, counts = value_day(snap, equity, {}, occ_meta, {})
    # equity 10*120 = 1200 ; option intrinsic 2*(120-100)*100 = 4000
    assert pos_value == D("5200")
    assert counts["equity_close"] == 1
    assert counts["opt_intrinsic"] == 1


# --- build fills from raw payloads --------------------------------------------

def test_build_stock_fills_executions():
    orders = [{
        "state": "filled", "side": "buy", "fees": "0.00",
        "instrument": "http://inst/AAA/",
        "last_transaction_at": "2026-01-05T15:30:00Z",
        "executions": [
            {"quantity": "3", "price": "100.00", "timestamp": "2026-01-05T15:30:00Z"},
            {"quantity": "2", "price": "101.00", "timestamp": "2026-01-05T15:31:00Z"},
        ],
    }]
    fills = build_stock_fills(orders, {"http://inst/AAA/": "AAA"})
    assert sum(f.qty_delta for f in fills) == D("5")
    assert sum(f.cash_delta for f in fills) == -(D("3") * D("100") + D("2") * D("101"))


def test_build_option_fills_short_open():
    orders = [{
        "state": "filled", "direction": "credit", "price": "2.10",
        "processed_quantity": "1",
        "last_transaction_at": "2026-06-01T15:30:00Z",
        "legs": [{"option": "http://opt/1/", "side": "sell", "ratio_quantity": "1"}],
    }]
    meta = {"http://opt/1/": OptMeta("SLS", date(2026, 8, 21), "P", D("90"))}
    fills, occ_meta = build_option_fills(orders, meta)
    assert len(fills) == 1
    assert fills[0].qty_delta == D("-1")          # sold to open = short
    assert fills[0].cash_delta == D("210")        # credit = cash in
    assert "SLS260821P00090000" in occ_meta


def test_build_cash_events_filters_dead_transfers():
    dividends = [
        {"state": "paid", "amount": "12.50", "paid_at": "2026-05-01T00:00:00Z"},
        {"state": "pending", "amount": "5.00", "paid_at": "2026-05-02T00:00:00Z"},
    ]
    transfers = [
        {"state": "completed", "amount": "1000", "direction": "deposit",
         "created_at": "2026-01-01T00:00:00Z"},
        {"state": "failed", "amount": "9999", "direction": "deposit",
         "created_at": "2026-01-02T00:00:00Z"},
        {"state": "completed", "amount": "300", "direction": "withdraw",
         "created_at": "2026-02-01T00:00:00Z"},
    ]
    events = build_cash_events(dividends, transfers)
    kinds = sorted(e.kind for e in events)
    assert kinds == ["deposit", "dividend", "withdrawal"]
    total = sum(e.cash_delta for e in events)
    assert total == D("12.50") + D("1000") - D("300")


# --- snapshot-date gating -----------------------------------------------------

def test_walk_back_never_emits_on_or_after_first_real_snapshot():
    # even if end were pushed forward, gating in value loop drops those; here we
    # assert ANCHOR_DATE itself is strictly before the first real snapshot.
    assert ANCHOR_DATE < FIRST_REAL_SNAPSHOT
    days, _, _ = walk_back({"AAA": D("1")}, D("0"),
                           [_fill(date(2026, 7, 1), "AAA", "1", "-100")],
                           [], start=date(2026, 7, 1))
    assert all(s.taken_on <= ANCHOR_DATE for s in days)
    assert all(s.taken_on < FIRST_REAL_SNAPSHOT for s in days)


# --- postgres-gated: write guard never overwrites a real row ------------------

TEST_ACCT = "BF-TEST-1"


@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs postgres (TEST_DATABASE_URL)")
    eng = create_engine(url)
    _cleanup(eng)
    yield eng
    _cleanup(eng)
    eng.dispose()


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM snapshots WHERE taken_on IN "
                          "(:d1, :d2)"),
                     {"d1": date(2026, 7, 10), "d2": FIRST_REAL_SNAPSHOT})


def test_write_snapshots_never_touches_real_row(pg_engine):
    # seed a REAL row on the first-real-snapshot date (no estimated flag)
    real_pa = {"robinhood:BF-TEST-1": {"value_usd": "12345.00"}}
    with pg_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
            "VALUES (:d, 12345.00, 1000.00, CAST(:pa AS jsonb))"),
            {"d": FIRST_REAL_SNAPSHOT, "pa": __import__("json").dumps(real_pa)})

    rows = [
        {  # gated out: on the real-snapshot date
            "taken_on": FIRST_REAL_SNAPSHOT,
            "total_value_usd": Decimal("999.00"),
            "cash_usd": Decimal("0"),
            "per_account": {"robinhood:BF-TEST-1": {"estimated": True}},
        },
        {  # allowed: strictly before
            "taken_on": date(2026, 7, 10),
            "total_value_usd": Decimal("500.00"),
            "cash_usd": Decimal("100.00"),
            "per_account": {"robinhood:BF-TEST-1": {"estimated": True,
                                                    "value_usd": "500.00"}},
        },
    ]
    written = write_snapshots(pg_engine, TEST_ACCT, rows)
    assert written == 1  # only the pre-go-live row

    with pg_engine.connect() as conn:
        real = conn.execute(text(
            "SELECT total_value_usd FROM snapshots WHERE taken_on = :d"),
            {"d": FIRST_REAL_SNAPSHOT}).scalar_one()
        est = conn.execute(text(
            "SELECT total_value_usd FROM snapshots WHERE taken_on = :d"),
            {"d": date(2026, 7, 10)}).scalar_one()
    assert real == Decimal("12345.00")   # untouched
    assert est == Decimal("500.00")      # written


def test_write_snapshots_updates_own_estimated_row(pg_engine):
    row_v1 = [{
        "taken_on": date(2026, 7, 10),
        "total_value_usd": Decimal("500.00"),
        "cash_usd": Decimal("100.00"),
        "per_account": {"robinhood:BF-TEST-1": {"estimated": True}},
    }]
    assert write_snapshots(pg_engine, TEST_ACCT, row_v1) == 1
    row_v2 = [{
        "taken_on": date(2026, 7, 10),
        "total_value_usd": Decimal("600.00"),
        "cash_usd": Decimal("100.00"),
        "per_account": {"robinhood:BF-TEST-1": {"estimated": True}},
    }]
    assert write_snapshots(pg_engine, TEST_ACCT, row_v2) == 1  # re-runnable
    with pg_engine.connect() as conn:
        val = conn.execute(text(
            "SELECT total_value_usd FROM snapshots WHERE taken_on = :d"),
            {"d": date(2026, 7, 10)}).scalar_one()
    assert val == Decimal("600.00")
