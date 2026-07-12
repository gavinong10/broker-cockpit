"""Integration test for GET /internal/performance.

Postgres-gated. The endpoint reads cash_flows + snapshots GLOBALLY (single
tenant, one RH account), so the fixture clears both tables (they are derived /
rebuildable), seeds one exact scenario, and asserts the headline numbers.

Scenario (integer-year exponents for an exact XIRR):
  deposit  1000 on 2025-01-01   (amount_usd +1000)
  withdraw  200 on 2026-01-01   (amount_usd  -200)
  value     990 on 2027-01-01   (observed terminal)
  -> net contributions = 1000 - 200 = 800
  -> dollar P&L        = 990 + 200 - 1000 = 190
  -> XIRR: -1000 + 200/1.1 + 990/1.21 = 0  =>  10.00%
"""
import json
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import settings
from app.main import app

TOKEN_HEADERS = {"X-Internal-Token": settings.internal_api_token}
RH_ACCOUNT = "PERF-TEST-RH"


def test_performance_requires_token():
    assert TestClient(app).get("/internal/performance").status_code == 401


@pytest.fixture
def pg_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs postgres (TEST_DATABASE_URL)")
    eng = create_engine(url)
    _reset(eng)
    _seed(eng)
    yield eng
    _reset(eng)
    eng.dispose()


@pytest.fixture
def client(pg_engine):
    import app.main as main
    prev = main._engine
    main._engine = pg_engine
    yield TestClient(app)
    main._engine = prev


def _reset(eng):
    with eng.begin() as conn:
        # cash_flows + snapshots are derived; clear fully for a deterministic
        # global read, then drop this test's account.
        conn.execute(text("DELETE FROM cash_flows"))
        conn.execute(text("DELETE FROM snapshots"))
        conn.execute(text("DELETE FROM broker_accounts WHERE external_id = :e"),
                     {"e": RH_ACCOUNT})


def _seed(eng):
    with eng.begin() as conn:
        acct = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd) "
            "VALUES ('robinhood', :e, 'USD', 990.00) RETURNING id"),
            {"e": RH_ACCOUNT}).scalar_one()
        for occ, kind, amt, ref in (
                ("2025-01-01", "deposit", "1000.00", "perf-test:dep"),
                ("2026-01-01", "withdrawal", "-200.00", "perf-test:wd")):
            conn.execute(text(
                "INSERT INTO cash_flows (broker_account_id, occurred_at, kind, "
                "amount_usd, currency, source_ref) "
                "VALUES (:a, :o, :k, :amt, 'USD', :ref)"),
                {"a": acct, "o": occ, "k": kind, "amt": amt, "ref": ref})
        # estimated pre-go-live snapshot (flag in per_account jsonb) ...
        conn.execute(text(
            "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account, source) "
            "VALUES (:d, 850.00, 0, CAST(:pa AS jsonb), 'observed')"),
            {"d": date(2025, 6, 1),
             "pa": json.dumps({f"robinhood:{RH_ACCOUNT}": {"estimated": True,
                                                           "value_usd": "850.00"}})})
        # ... and the observed terminal row (today's exact value).
        conn.execute(text(
            "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account, source) "
            "VALUES (:d, 990.00, 990.00, CAST(:pa AS jsonb), 'observed')"),
            {"d": date(2027, 1, 1),
             "pa": json.dumps({f"robinhood:{RH_ACCOUNT}": {"value_usd": "990.00"}})})


def test_performance_inception_headline_numbers(client):
    r = client.get("/internal/performance?period=inception", headers=TOKEN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "inception"
    assert body["available"] is True
    assert body["headline_metric"] == "annualized"  # annualized only for inception
    assert body["dollar_pnl_usd"] == "190.00"
    assert body["net_contributions_usd"] == "800.00"
    assert body["current_value_usd"] == "990.00"
    assert abs(float(body["mwr_annualized_pct"]) - 10.00) < 0.01
    # cumulative = period P&L / gross deposits = 190 / 1000 = 19.00%
    assert body["cumulative_return_pct"] == "19.00"
    assert body["solid"] is True
    assert body["boundary_estimated"] is False
    # value_series carries the estimated flag straight through
    est = {p["date"]: p["estimated"] for p in body["value_series"]}
    assert est["2025-06-01"] is True
    assert est["2027-01-01"] is False
    # contributions step up on deposit, down on withdrawal
    contrib = {p["date"]: p["value_usd"] for p in body["contributions_series"]}
    assert contrib["2025-01-01"] == "1000.00"
    assert contrib["2026-01-01"] == "800.00"


def test_performance_subperiod_cumulative_headline(client):
    # 1y boundary = the estimated 2025-06-01 snapshot (value 850). Period holds
    # only the +200 withdrawal. cumulative = period P&L / opening value
    #   = (990 - 850 + 200) / 850 = 340/850 = 40.00%.
    r = client.get("/internal/performance?period=1y", headers=TOKEN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["headline_metric"] == "cumulative"  # NOT annualized on sub-periods
    assert body["cumulative_return_pct"] == "40.00"
    assert body["mwr_annualized_pct"] is not None    # annualized still provided
    assert body["solid"] is False
    assert body["boundary_estimated"] is True         # estimated opening value


def test_performance_period_without_boundary_is_unavailable(client):
    # YTD boundary falls on the terminal day itself (only two snapshots seeded),
    # so the XIRR span is zero -> gracefully unavailable, never a wrong number.
    r = client.get("/internal/performance?period=ytd", headers=TOKEN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == "insufficient history for this period"
    # the chart series still ship so the toggle can render the chart
    assert len(body["value_series"]) == 2


def test_performance_inception_always_available_with_one_snapshot(pg_engine):
    # Strip to a single observed snapshot: since-inception must still be available
    # (dollar P&L + XIRR need only flows + today's value), even with no daily tail.
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM snapshots WHERE taken_on = '2025-06-01'"))
    import app.main as main
    prev = main._engine
    main._engine = pg_engine
    try:
        c = TestClient(app)
        body = c.get("/internal/performance?period=inception", headers=TOKEN_HEADERS).json()
    finally:
        main._engine = prev
    assert body["available"] is True
    assert body["dollar_pnl_usd"] == "190.00"
    assert body["twr_pct"] is None  # only one daily point -> no TWR


def test_performance_bad_period_400(client):
    assert client.get("/internal/performance?period=nope",
                      headers=TOKEN_HEADERS).status_code == 400
