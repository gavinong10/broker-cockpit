import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.heartbeat import seconds_until_next
from app.snapshots import compute_snapshot, record_snapshot

TEST_ACCOUNT = "SNAP-TEST-1"
TEST_SYMBOLS = ("SNAPQ", "SNAPQ261218C00100000", "SNAPQ260821P00090000")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()

# Seeded account: 10 SNAPQ @ 195.50 = 1955.00
#                 +2 calls @ 5.25 mark x100 = 1050.00
#                 -1 short put @ 2.10 mark x100 = -210.00
#                 cash 1234.50
# account total = 4029.50
EXPECTED_ACCOUNT_VALUE = Decimal("4029.50")
EXPECTED_ACCOUNT_CASH = Decimal("1234.50")


# --- pure unit tests: shared timing helper with minute offset -----------------

def test_seconds_until_next_supports_minutes():
    now = datetime(2026, 7, 10, 21, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now, minute=10) == 600


def test_seconds_until_next_minute_already_passed_waits_until_tomorrow():
    now = datetime(2026, 7, 10, 21, 15, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now, minute=10) == 24 * 3600 - 5 * 60


def test_seconds_until_next_exactly_at_minute_waits_a_full_day():
    now = datetime(2026, 7, 10, 21, 10, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now, minute=10) == 24 * 3600


def test_seconds_until_next_default_minute_is_zero():
    now = datetime(2026, 7, 10, 20, 0, 0, tzinfo=timezone.utc)
    assert seconds_until_next(21, now) == 3600


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


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE broker='robinhood' AND external_id=:e)"),
            {"e": TEST_ACCOUNT})
        conn.execute(text(
            "DELETE FROM broker_accounts WHERE broker='robinhood' AND external_id=:e"),
            {"e": TEST_ACCOUNT})
        conn.execute(text("DELETE FROM instruments WHERE symbol = ANY(:syms)"),
                     {"syms": list(TEST_SYMBOLS)})
        conn.execute(text("DELETE FROM snapshots WHERE taken_on = :d"),
                     {"d": _today_utc()})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category = 'snapshot.recorded' "
            "AND payload->>'taken_on' = :d"),
            {"d": _today_utc().isoformat()})


def _seed(eng):
    with eng.begin() as conn:
        acct_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd) "
            "VALUES ('robinhood', :e, 'USD', :cash) RETURNING id"),
            {"e": TEST_ACCOUNT, "cash": EXPECTED_ACCOUNT_CASH}).scalar_one()
        # STK with NULL multiplier -> must default to 1
        stk_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency) "
            "VALUES ('SNAPQ', 'STK', 'USD') RETURNING id")).scalar_one()
        call_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES ('SNAPQ261218C00100000', 'OPT', 'USD', '2026-12-18', 100, 'C', 100) "
            "RETURNING id")).scalar_one()
        put_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES ('SNAPQ260821P00090000', 'OPT', 'USD', '2026-08-21', 90, 'P', 100) "
            "RETURNING id")).scalar_one()
        for iid, qty, price in ((stk_id, "10", "195.50"),
                                (call_id, "2", "5.25"),
                                (put_id, "-1", "2.10")):
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, last_price_usd) "
                "VALUES (:a, :i, :q, :p)"),
                {"a": acct_id, "i": iid, "q": qty, "p": price})


def test_compute_snapshot_totals(pg_engine):
    snap = compute_snapshot(pg_engine)

    assert snap["taken_on"] == _today_utc()

    key = f"robinhood:{TEST_ACCOUNT}"
    assert key in snap["per_account"]
    acct = snap["per_account"][key]
    # per-account jsonb payload carries strings (JSON-safe Decimals)
    assert Decimal(acct["value_usd"]) == EXPECTED_ACCOUNT_VALUE
    assert Decimal(acct["cash_usd"]) == EXPECTED_ACCOUNT_CASH
    assert Decimal(acct["positions_value_usd"]) == EXPECTED_ACCOUNT_VALUE - EXPECTED_ACCOUNT_CASH

    # totals are internally consistent: sum of per-account breakdowns
    # (the shared test DB may contain other accounts' rows)
    assert isinstance(snap["total_value_usd"], Decimal)
    assert snap["total_value_usd"] == sum(
        Decimal(a["value_usd"]) for a in snap["per_account"].values())
    assert snap["cash_usd"] == sum(
        Decimal(a["cash_usd"]) for a in snap["per_account"].values())


def test_record_snapshot_upserts_single_row_per_day(pg_engine):
    first = record_snapshot(pg_engine)
    second = record_snapshot(pg_engine)  # same day -> upsert, not a second row

    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT taken_on, total_value_usd, cash_usd, per_account "
            "FROM snapshots WHERE taken_on = :d"), {"d": _today_utc()}).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.taken_on == _today_utc()
    assert row.total_value_usd == second["total_value_usd"]
    assert row.cash_usd == second["cash_usd"]
    assert f"robinhood:{TEST_ACCOUNT}" in row.per_account
    assert first["taken_on"] == second["taken_on"] == _today_utc()

    # audit trail: snapshot.recorded carries the total
    with pg_engine.connect() as conn:
        n_audit = conn.execute(text(
            "SELECT count(*) FROM audit_log WHERE category = 'snapshot.recorded' "
            "AND payload->>'total_value_usd' = :t"),
            {"t": str(second["total_value_usd"])}).scalar_one()
    assert n_audit >= 2
