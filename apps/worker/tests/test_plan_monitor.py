"""Plan monitor tests: OCC parsing, classification boundaries, monitoring pass.

Postgres-gated tests seed one open basket (plnmon-test) with two pending plan
legs and run monitor_plans with an injected quote_fn — no Robinhood session is
touched. Alerts are captured by monkeypatching app.plan_monitor.alert.
Seeds are namespaced (plnmon-test / PLNM*) and cleaned before/after.
"""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.plan_monitor import (StructureQuote, classify, monitor_plans,
                              parse_occ, structure_width)

SLUG = "plnmon-test"

VERTICAL = [
    {"occ": "PLNM281215C00220000", "sec_type": "OPT", "ratio": 1},
    {"occ": "PLNM281215C00330000", "sec_type": "OPT", "ratio": -1},
]
# distinct strike from VERTICAL's legs: graduation tests seed positions for the
# vertical's contracts and must not accidentally cover this leg too
SINGLE = [{"occ": "PLNM281215C00550000", "sec_type": "OPT", "ratio": 1}]


# --- pure functions ---------------------------------------------------------------

def test_parse_occ_roundtrip():
    sym, expiry, right, strike = parse_occ("PLNM281215C00220000")
    assert (sym, expiry, right, strike) == ("PLNM", date(2028, 12, 15), "C",
                                            Decimal("220"))
    with pytest.raises(ValueError):
        parse_occ("not-an-occ")


def test_structure_width():
    assert structure_width(VERTICAL) == Decimal("110")
    assert structure_width(SINGLE) is None                       # no cap
    assert structure_width([{"symbol": "PLNM", "sec_type": "STK", "ratio": 1}]) is None
    mixed_expiry = [
        {"occ": "PLNM281215C00220000", "sec_type": "OPT", "ratio": 1},
        {"occ": "PLNM270115C00330000", "sec_type": "OPT", "ratio": -1},
    ]
    assert structure_width(mixed_expiry) is None                 # calendar, not vertical


def test_classify_boundaries():
    planned, tol, width = Decimal("17.23"), Decimal("5"), Decimal("110")
    limit = planned * Decimal("1.05")
    assert classify(planned, tol, None, width) == "unquotable"
    assert classify(planned, tol, limit, width) == "in_window"          # exactly at tolerance
    assert classify(planned, tol, limit + Decimal("0.01"), width) == "drifted"
    assert classify(planned, tol, width * Decimal("0.8"), width) == "thesis_stale"  # 80% edge
    # stale outranks in_window/drifted regardless of tolerance
    assert classify(Decimal("100"), Decimal("100"), Decimal("95"), width) == "thesis_stale"
    # no width => never stale, only window math
    assert classify(planned, tol, Decimal("1000"), None) == "drifted"


# --- postgres-gated monitoring pass -----------------------------------------------

@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs postgres (TEST_DATABASE_URL)")
    eng = create_engine(url)
    _cleanup(eng)
    _seed(eng)
    yield eng
    _cleanup(eng)
    eng.dispose()


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM basket_plan_marks WHERE plan_leg_id IN "
            "(SELECT pl.id FROM basket_plan_legs pl "
            " JOIN baskets b ON b.id = pl.basket_id WHERE b.slug = :s)"), {"s": SLUG})
        conn.execute(text(
            "DELETE FROM basket_plan_legs WHERE basket_id IN "
            "(SELECT id FROM baskets WHERE slug = :s)"), {"s": SLUG})
        conn.execute(text("DELETE FROM baskets WHERE slug = :s"), {"s": SLUG})


def _seed(eng):
    with eng.begin() as conn:
        basket_id = conn.execute(text(
            "INSERT INTO baskets (slug, name, thesis) "
            "VALUES (:s, 'Plan monitor test', 'test') RETURNING id"),
            {"s": SLUG}).scalar_one()
        for label, structure, debit in (
                ("PLNM vertical", VERTICAL, "17.23"),
                ("PLNM single", SINGLE, "30.00")):
            conn.execute(text(
                "INSERT INTO basket_plan_legs (basket_id, label, structure, qty, "
                "planned_net_debit) VALUES (:b, :l, CAST(:st AS jsonb), 1, :d)"),
                {"b": basket_id, "l": label,
                 "st": __import__("json").dumps(structure), "d": debit})


def _legs(eng):
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT pl.label, pl.monitor_status, pl.last_quote_net, "
            "pl.last_alerted_status, pl.last_quoted_at "
            "FROM basket_plan_legs pl JOIN baskets b ON b.id = pl.basket_id "
            "WHERE b.slug = :s ORDER BY pl.id"), {"s": SLUG}).all()
    return {r.label: r for r in rows}


def _marks(eng, label):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT m.net_cost, m.underlying_spot, m.quote_basis "
            "FROM basket_plan_marks m JOIN basket_plan_legs pl ON pl.id = m.plan_leg_id "
            "JOIN baskets b ON b.id = pl.basket_id "
            "WHERE b.slug = :s AND pl.label = :l ORDER BY m.id"),
            {"s": SLUG, "l": label}).all()


def _capture_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr("app.plan_monitor.alert",
                        lambda title, desc: calls.append((title, desc)))
    return calls


def test_monitor_pass_marks_statuses_and_alerts(pg_engine, monkeypatch):
    calls = _capture_alerts(monkeypatch)
    quotes = {
        "PLNM281215C00220000": StructureQuote(Decimal("16.90"), "mid", Decimal("221.5")),
    }

    def quote_fn(structure):
        if structure == VERTICAL:
            return StructureQuote(Decimal("16.90"), "mid", Decimal("221.5"))
        return StructureQuote(None, None, None)   # single leg unquotable

    summary = monitor_plans(pg_engine, quote_fn=quote_fn)
    assert summary == {"checked": 2, "alerted": 1,
                       "statuses": {"in_window": 1, "unquotable": 1}}

    legs = _legs(pg_engine)
    vert = legs["PLNM vertical"]
    assert vert.monitor_status == "in_window"
    assert Decimal(vert.last_quote_net) == Decimal("16.90")
    assert vert.last_alerted_status == "in_window"
    assert vert.last_quoted_at is not None
    single = legs["PLNM single"]
    assert single.monitor_status == "unquotable"
    assert single.last_alerted_status is None            # unquotable never alerts

    assert len(calls) == 1 and "Entry window open" in calls[0][0]
    marks = _marks(pg_engine, "PLNM vertical")
    assert len(marks) == 1
    assert Decimal(marks[0].net_cost) == Decimal("16.90")
    assert marks[0].quote_basis == "mid"
    un_marks = _marks(pg_engine, "PLNM single")
    assert len(un_marks) == 1 and un_marks[0].net_cost is None


def test_alert_dedupe_and_transition(pg_engine, monkeypatch):
    calls = _capture_alerts(monkeypatch)
    in_window = lambda s: StructureQuote(Decimal("16.90"), "mid", Decimal("221.5"))
    drifted = lambda s: StructureQuote(Decimal("40.00"), "mark", Decimal("260"))

    monitor_plans(pg_engine, quote_fn=in_window)   # both legs quote 16.90/40? single too
    n_first = len(calls)
    monitor_plans(pg_engine, quote_fn=in_window)   # same statuses: no new alerts
    assert len(calls) == n_first

    monitor_plans(pg_engine, quote_fn=drifted)     # vertical -> drifted (40 >= 0.8*110? no: 88) => drifted
    legs = _legs(pg_engine)
    assert legs["PLNM vertical"].monitor_status == "drifted"
    assert any("Plan drifted" in t for t, _ in calls[n_first:])

    # back into the window -> alerts again (transition, not repeat)
    before = len(calls)
    monitor_plans(pg_engine, quote_fn=in_window)
    assert any("Entry window open" in t for t, _ in calls[before:])

    # three passes for the vertical leg => three marks
    assert len(_marks(pg_engine, "PLNM vertical")) == 4


def test_thesis_stale_for_run_through_vertical(pg_engine, monkeypatch):
    calls = _capture_alerts(monkeypatch)
    ran_away = lambda s: StructureQuote(Decimal("95.00"), "mid", Decimal("340"))
    monitor_plans(pg_engine, quote_fn=ran_away)
    legs = _legs(pg_engine)
    assert legs["PLNM vertical"].monitor_status == "thesis_stale"   # 95 >= 88 = 0.8*110
    # the single call has no width: 95 > 30*1.05 => drifted, never stale
    assert legs["PLNM single"].monitor_status == "drifted"
    titles = [t for t, _ in calls]
    assert any("thesis stale" in t.lower() for t in titles)


def test_closed_baskets_and_held_legs_skipped(pg_engine, monkeypatch):
    _capture_alerts(monkeypatch)
    with pg_engine.begin() as conn:
        conn.execute(text(
            "UPDATE basket_plan_legs SET status = 'held' WHERE label = 'PLNM single' "
            "AND basket_id IN (SELECT id FROM baskets WHERE slug = :s)"), {"s": SLUG})
    summary = monitor_plans(pg_engine,
                            quote_fn=lambda s: StructureQuote(Decimal("10"), "mid", None))
    assert summary["checked"] == 1                       # held leg skipped
    with pg_engine.begin() as conn:
        conn.execute(text("UPDATE baskets SET status = 'closed' WHERE slug = :s"),
                     {"s": SLUG})
    summary = monitor_plans(pg_engine,
                            quote_fn=lambda s: StructureQuote(Decimal("10"), "mid", None))
    assert summary == {"checked": 0, "alerted": 0}       # closed basket skipped


def test_quote_fn_exception_isolated(pg_engine, monkeypatch):
    _capture_alerts(monkeypatch)

    def flaky(structure):
        if structure == VERTICAL:
            raise RuntimeError("boom")
        return StructureQuote(Decimal("29.00"), "mid", Decimal("221"))

    summary = monitor_plans(pg_engine, quote_fn=flaky)
    assert summary["checked"] == 2
    legs = _legs(pg_engine)
    assert legs["PLNM vertical"].monitor_status == "unquotable"   # error -> unquotable
    assert legs["PLNM single"].monitor_status == "in_window"      # 29 <= 30*1.05


# --- Task 8: graduation ------------------------------------------------------------

ACCOUNT = "PLNMON-TEST-RH"


def _seed_positions(eng, contracts):
    """contracts: list of (symbol, sec_type, qty, avg_cost)."""
    from datetime import datetime, timezone
    with eng.begin() as conn:
        acct = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, "
            "last_synced_at) VALUES ('robinhood', :e, 'USD', 0, :ts) RETURNING id"),
            {"e": ACCOUNT, "ts": datetime.now(timezone.utc)}).scalar_one()
        for symbol, sec_type, qty, avg in contracts:
            iid = conn.execute(text(
                "INSERT INTO instruments (symbol, sec_type, currency, multiplier) "
                "VALUES (:s, :t, 'USD', :m) RETURNING id"),
                {"s": symbol, "t": sec_type,
                 "m": 100 if sec_type == "OPT" else None}).scalar_one()
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, avg_cost_usd) "
                "VALUES (:a, :i, :q, :c)"), {"a": acct, "i": iid, "q": qty, "c": avg})


def _cleanup_positions(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE external_id = :e)"), {"e": ACCOUNT})
        conn.execute(text("DELETE FROM broker_accounts WHERE external_id = :e"),
                     {"e": ACCOUNT})
        conn.execute(text("DELETE FROM instruments WHERE symbol LIKE 'PLNM%'"))


@pytest.fixture
def pg_positions(pg_engine):
    _cleanup_positions(pg_engine)
    yield pg_engine
    _cleanup_positions(pg_engine)


def test_graduation_partial_then_held_with_fill_quality(pg_positions, monkeypatch):
    from app.plan_monitor import graduate_plans
    eng = pg_positions
    calls = _capture_alerts(monkeypatch)

    # only the long leg synced -> partial, no alert
    _seed_positions(eng, [("PLNM281215C00220000", "OPT", "1", "19.00")])
    summary = graduate_plans(eng)
    assert summary["graduated"] == 0
    assert _legs(eng)["PLNM vertical"].monitor_status is None   # untouched by graduation
    with eng.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM basket_plan_legs WHERE label = 'PLNM vertical'")).scalar_one()
    assert status == "partial"
    assert calls == []

    # short leg arrives (qty -1) -> held, fill = 19.00 - 2.50 = 16.50, alert once
    _cleanup_positions(eng)
    _seed_positions(eng, [("PLNM281215C00220000", "OPT", "1", "19.00"),
                          ("PLNM281215C00330000", "OPT", "-1", "2.50")])
    summary = graduate_plans(eng)
    assert summary["graduated"] == 1
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT status, filled_net_debit FROM basket_plan_legs "
            "WHERE label = 'PLNM vertical'")).one()
    assert row.status == "held"
    assert Decimal(row.filled_net_debit) == Decimal("16.50")
    fills = [t for t, _ in calls if "filled" in t.lower()]
    assert len(fills) == 1 and "PLNM vertical" in fills[0]
    # slippage vs plan 17.23: 16.50/17.23-1 = -4.2%
    assert any("-4.2% slippage" in d for _, d in calls)

    # idempotent: second pass does not re-alert or re-grade
    n = len(calls)
    summary = graduate_plans(eng)
    assert summary["graduated"] == 0 and len(calls) == n


def test_graduated_leg_leaves_monitor_pool(pg_positions, monkeypatch):
    from app.plan_monitor import graduate_plans
    eng = pg_positions
    _capture_alerts(monkeypatch)
    _seed_positions(eng, [("PLNM281215C00220000", "OPT", "1", "19.00"),
                          ("PLNM281215C00330000", "OPT", "-1", "2.50")])
    graduate_plans(eng)
    summary = monitor_plans(eng, quote_fn=lambda s: StructureQuote(Decimal("10"), "mid", None))
    assert summary["checked"] == 1   # only the single-call leg remains pending
