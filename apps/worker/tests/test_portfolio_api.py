"""Portfolio internal API tests.

Postgres-gated tests seed two accounts (one fresh, one 3h stale) holding an
overlapping equity plus a long call and a short put, then exercise the three
endpoints through TestClient. Seeds are namespaced (PORT-TEST-*/PORTQ*) and
cleaned up before and after, mirroring tests/test_snapshots.py hygiene.

Seeded book:
  RH  (fresh): cash 1000.00 | PORTQ 10 @ avg 90  | call +2 @ avg 4.00
  IB  (3h old): cash 500.00 | PORTQ  5 @ avg 120 | put  -1 @ avg 3.00
  PORTQ: last 110, prev 105 -> qty 15, weighted avg (10*90+5*120)/15 = 100,
         MV 1650, day (110-105)*15 = 75, unrealized (110-100)*15 = 150
  call (x100): last 5.25, prev 5.00 -> MV 1050, day 50, unrealized 250
  put  (x100): last 2.10, prev 2.50 -> MV -210, day +40, unrealized +90
"""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import settings
from app.main import app

TOKEN_HEADERS = {"X-Internal-Token": settings.internal_api_token}

RH_ACCOUNT = "PORT-TEST-RH"
IB_ACCOUNT = "PORT-TEST-IB"
TEST_ACCOUNTS = (RH_ACCOUNT, IB_ACCOUNT)

STK = "PORTQ"
CALL = "PORTQ261218C00100000"
PUT = "PORTQ260821P00090000"
TEST_SYMBOLS = (STK, CALL, PUT)

# snapshot seeds: offsets in days from today, with sentinel totals
SNAP_SEEDS = {10: Decimal("1111.11"), 100: Decimal("2222.22"), 3700: Decimal("3333.33")}


def _snap_dates():
    today = datetime.now(timezone.utc).date()
    return {offset: today - timedelta(days=offset) for offset in SNAP_SEEDS}


# --- auth: all three routes 401 without token (no DB needed) ------------------

def test_all_routes_require_token():
    c = TestClient(app)
    assert c.get("/internal/portfolio").status_code == 401
    assert c.get("/internal/positions/AAPL").status_code == 401
    assert c.get("/internal/snapshots").status_code == 401


# --- postgres-gated tests -----------------------------------------------------

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


@pytest.fixture
def client(pg_engine):
    """TestClient whose app reads from the seeded test engine."""
    import app.main as main
    prev = main._engine
    main._engine = pg_engine
    yield TestClient(app)
    main._engine = prev


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE external_id = ANY(:e))"),
            {"e": list(TEST_ACCOUNTS)})
        conn.execute(text(
            "DELETE FROM broker_accounts WHERE external_id = ANY(:e)"),
            {"e": list(TEST_ACCOUNTS)})
        conn.execute(text("DELETE FROM instruments WHERE symbol = ANY(:syms)"),
                     {"syms": list(TEST_SYMBOLS)})
        conn.execute(text("DELETE FROM snapshots WHERE taken_on = ANY(:d)"),
                     {"d": list(_snap_dates().values())})


def _seed(eng):
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        rh_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('robinhood', :e, 'USD', 1000.00, :ts) RETURNING id"),
            {"e": RH_ACCOUNT, "ts": now}).scalar_one()
        ib_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('ibkr', :e, 'USD', 500.00, :ts) RETURNING id"),
            {"e": IB_ACCOUNT, "ts": now - timedelta(hours=3)}).scalar_one()
        # STK with NULL multiplier -> must be treated as 1
        stk_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency) "
            "VALUES (:s, 'STK', 'USD') RETURNING id"), {"s": STK}).scalar_one()
        call_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', '2026-12-18', 100, 'C', 100) RETURNING id"),
            {"s": CALL}).scalar_one()
        put_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', '2026-08-21', 90, 'P', 100) RETURNING id"),
            {"s": PUT}).scalar_one()
        for acct, iid, qty, avg, last, prev in (
                (rh_id, stk_id, "10", "90", "110", "105"),
                (ib_id, stk_id, "5", "120", "110", "105"),
                (rh_id, call_id, "2", "4.00", "5.25", "5.00"),
                (ib_id, put_id, "-1", "3.00", "2.10", "2.50")):
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, "
                "avg_cost_usd, last_price_usd, prev_close_usd) "
                "VALUES (:a, :i, :q, :c, :l, :p)"),
                {"a": acct, "i": iid, "q": qty, "c": avg, "l": last, "p": prev})
        for offset, total in SNAP_SEEDS.items():
            conn.execute(text(
                "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
                "VALUES (:d, :t, 0, '{}'::jsonb) "
                "ON CONFLICT (taken_on) DO UPDATE SET total_value_usd = EXCLUDED.total_value_usd"),
                {"d": _snap_dates()[offset], "t": total})


def _get_portfolio(client):
    resp = client.get("/internal/portfolio", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    return resp.json()


def _find_position(body, symbol):
    rows = [p for p in body["positions"] if p["symbol"] == symbol]
    assert len(rows) == 1, f"expected exactly one row for {symbol}"
    return rows[0]


# --- GET /internal/portfolio ---------------------------------------------------

def test_portfolio_aggregates_same_symbol_across_brokers(client):
    body = _get_portfolio(client)
    stk = _find_position(body, STK)
    assert stk["sec_type"] == "STK"
    assert Decimal(stk["qty"]) == 15
    assert Decimal(stk["avg_cost_usd"]) == 100  # weighted: (10*90 + 5*120) / 15
    assert Decimal(stk["last_price_usd"]) == Decimal("110")
    assert Decimal(stk["prev_close_usd"]) == Decimal("105")
    assert Decimal(stk["market_value_usd"]) == Decimal("1650.00")
    assert Decimal(stk["unrealized_pl_usd"]) == Decimal("150.00")
    brokers = {b["broker"]: Decimal(b["qty"]) for b in stk["brokers"]}
    assert brokers == {"robinhood": Decimal("10"), "ibkr": Decimal("5")}


def test_portfolio_option_math_uses_multiplier_100(client):
    body = _get_portfolio(client)
    call = _find_position(body, CALL)
    assert call["sec_type"] == "OPT"
    assert Decimal(call["market_value_usd"]) == Decimal("1050.00")  # 2 * 5.25 * 100
    assert Decimal(call["unrealized_pl_usd"]) == Decimal("250.00")
    assert call["expiry"] == "2026-12-18"
    assert Decimal(call["strike"]) == 100
    assert call["right"] == "C"


def test_portfolio_short_option_negative_market_value(client):
    body = _get_portfolio(client)
    put = _find_position(body, PUT)
    assert Decimal(put["qty"]) == -1
    assert Decimal(put["market_value_usd"]) == Decimal("-210.00")  # -1 * 2.10 * 100
    assert Decimal(put["unrealized_pl_usd"]) == Decimal("90.00")   # short gained
    assert put["right"] == "P"


def test_portfolio_day_change_uses_prev_close(client):
    body = _get_portfolio(client)
    assert Decimal(_find_position(body, STK)["day_change_usd"]) == Decimal("75.00")
    assert Decimal(_find_position(body, CALL)["day_change_usd"]) == Decimal("50.00")
    assert Decimal(_find_position(body, PUT)["day_change_usd"]) == Decimal("40.00")
    # top-level pct is self-consistent: day / (total - day) * 100
    total = Decimal(body["total_value_usd"])
    day = Decimal(body["day_change_usd"])
    if total != day:
        expected = day / (total - day) * 100
        assert abs(Decimal(body["day_change_pct"]) - expected) < Decimal("0.01")


def test_portfolio_weights_sum_to_100_including_cash(client):
    body = _get_portfolio(client)
    total = Decimal(body["total_value_usd"])
    assert total != 0
    weights = sum(Decimal(p["weight_pct"]) for p in body["positions"])
    cash_weight = Decimal(body["cash_usd"]) / total * 100
    assert abs(weights + cash_weight - 100) < Decimal("0.05")


def test_portfolio_sorted_by_market_value_desc(client):
    body = _get_portfolio(client)
    values = [Decimal(p["market_value_usd"]) for p in body["positions"]]
    assert values == sorted(values, reverse=True)


def test_portfolio_staleness_honors_is_stale(client):
    body = _get_portfolio(client)
    accounts = {a["external_id"]: a for a in body["accounts"]}
    assert accounts[RH_ACCOUNT]["broker"] == "robinhood"
    assert accounts[RH_ACCOUNT]["stale"] is False   # synced just now
    assert accounts[IB_ACCOUNT]["stale"] is True    # 3h > both thresholds
    assert accounts[RH_ACCOUNT]["last_synced_at"] is not None


# --- GET /internal/positions/{symbol} ------------------------------------------

def test_position_detail_aggregate_and_per_account_rows(client):
    resp = client.get(f"/internal/positions/{STK}", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == STK
    assert Decimal(body["qty"]) == 15
    assert Decimal(body["avg_cost_usd"]) == 100
    assert Decimal(body["market_value_usd"]) == Decimal("1650.00")
    per = {a["external_id"]: a for a in body["accounts"]}
    assert set(per) == {RH_ACCOUNT, IB_ACCOUNT}
    rh = per[RH_ACCOUNT]
    assert rh["broker"] == "robinhood"
    assert Decimal(rh["qty"]) == 10
    assert Decimal(rh["avg_cost_usd"]) == 90
    assert Decimal(rh["market_value_usd"]) == Decimal("1100.00")
    assert Decimal(rh["unrealized_pl_usd"]) == Decimal("200.00")   # (110-90)*10
    ib = per[IB_ACCOUNT]
    assert Decimal(ib["market_value_usd"]) == Decimal("550.00")
    assert Decimal(ib["unrealized_pl_usd"]) == Decimal("-50.00")   # (110-120)*5


def test_position_detail_unknown_symbol_404(client):
    resp = client.get("/internal/positions/ZZZNOSUCH", headers=TOKEN_HEADERS)
    assert resp.status_code == 404
    assert resp.json()  # JSON body, not a bare error


# --- GET /internal/snapshots ----------------------------------------------------

def _snap_map(rows):
    return {r["taken_on"]: Decimal(r["total_value_usd"]) for r in rows}


def test_snapshots_default_90_days(client):
    resp = client.get("/internal/snapshots", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    rows = resp.json()
    dates = [r["taken_on"] for r in rows]
    assert dates == sorted(dates)  # ascending
    got = _snap_map(rows)
    d = {k: v.isoformat() for k, v in _snap_dates().items()}
    assert got.get(d[10]) == SNAP_SEEDS[10]
    assert d[100] not in got
    assert d[3700] not in got


def test_snapshots_days_param_widens_window(client):
    resp = client.get("/internal/snapshots?days=200", headers=TOKEN_HEADERS)
    got = _snap_map(resp.json())
    d = {k: v.isoformat() for k, v in _snap_dates().items()}
    assert got.get(d[10]) == SNAP_SEEDS[10]
    assert got.get(d[100]) == SNAP_SEEDS[100]
    assert d[3700] not in got


def test_snapshots_days_capped_at_3650(client):
    resp = client.get("/internal/snapshots?days=99999", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    got = _snap_map(resp.json())
    d = {k: v.isoformat() for k, v in _snap_dates().items()}
    assert got.get(d[100]) == SNAP_SEEDS[100]
    assert d[3700] not in got  # beyond the 3650-day cap
