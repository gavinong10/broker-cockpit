import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.robinhood import normalize, upsert

FIXTURES = Path(__file__).parent / "fixtures"
TEST_ACCOUNT = "5RH12345"
TEST_SYMBOLS = ("AAPL", "NVDA", "AAPL261218C00150000", "NVDA260821P00115000")


def load_raw() -> dict:
    stock = json.loads((FIXTURES / "rh_stock_positions.json").read_text())
    opt = json.loads((FIXTURES / "rh_option_positions.json").read_text())
    return {
        "account_profile": stock["account_profile"],
        "stock_positions": stock["positions"],
        "instruments": stock["instruments"],
        "quotes": stock["quotes"],
        "option_positions": opt["positions"],
        "option_instruments": opt["instruments"],
        "option_marks": opt["marks"],
    }


def test_normalize_equities():
    account, rows = normalize(load_raw())
    assert account.external_id == TEST_ACCOUNT
    assert account.cash_usd == Decimal("1234.56")

    stk = {r.symbol: r for r in rows if r.sec_type == "STK"}
    assert set(stk) == {"AAPL", "NVDA"}

    aapl = stk["AAPL"]
    assert aapl.qty == Decimal("10")
    assert aapl.avg_cost_usd == Decimal("187.2345")
    assert aapl.last_price_usd == Decimal("195.12")
    assert aapl.prev_close_usd == Decimal("193.40")

    nvda = stk["NVDA"]
    assert isinstance(nvda.qty, Decimal)
    assert nvda.qty == Decimal("0.437215")  # fractional shares preserved exactly
    assert nvda.avg_cost_usd == Decimal("121.50")


def test_normalize_options():
    _, rows = normalize(load_raw())
    opts = {r.right: r for r in rows if r.sec_type == "OPT"}
    assert set(opts) == {"C", "P"}

    call = opts["C"]  # long 2x AAPL 150C 2026-12-18
    assert call.symbol == "AAPL261218C00150000"  # OCC: SYM + YYMMDD + C/P + strike*1000 %08d
    assert call.qty == Decimal("2")
    assert call.avg_cost_usd == Decimal("5.125")  # 512.50 per contract / 100
    assert call.last_price_usd == Decimal("48.15")
    assert call.prev_close_usd == Decimal("46.90")
    assert call.multiplier == 100
    assert call.expiry == date(2026, 12, 18)
    assert call.strike == Decimal("150")
    assert call.right == "C"

    put = opts["P"]  # short 1x NVDA 115P 2026-08-21
    assert put.symbol == "NVDA260821P00115000"
    assert put.qty == Decimal("-1")  # type=short -> negative qty
    assert put.avg_cost_usd == Decimal("2.30")
    assert put.last_price_usd == Decimal("2.05")
    assert put.multiplier == 100
    assert put.expiry == date(2026, 8, 21)
    assert put.strike == Decimal("115")
    assert put.right == "P"


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
        conn.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE broker='robinhood' AND external_id=:e)"),
            {"e": TEST_ACCOUNT})
        conn.execute(text(
            "DELETE FROM broker_accounts WHERE broker='robinhood' AND external_id=:e"),
            {"e": TEST_ACCOUNT})
        conn.execute(text("DELETE FROM instruments WHERE symbol = ANY(:syms)"),
                     {"syms": list(TEST_SYMBOLS)})


def _position_rows(eng):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT i.symbol, p.qty, p.last_price_usd, p.prev_close_usd "
            "FROM positions p "
            "JOIN broker_accounts a ON a.id = p.broker_account_id "
            "JOIN instruments i ON i.id = p.instrument_id "
            "WHERE a.broker = 'robinhood' AND a.external_id = :e ORDER BY i.symbol"),
            {"e": TEST_ACCOUNT}).all()


def test_upsert_idempotent(pg_engine):
    account, rows = normalize(load_raw())

    upsert(pg_engine, account, rows)
    first = _position_rows(pg_engine)
    assert len(first) == 4

    # idempotent: same payload again -> same rows, no duplicates
    upsert(pg_engine, account, rows)
    second = _position_rows(pg_engine)
    assert second == first

    # instruments not duplicated either
    with pg_engine.connect() as conn:
        n_instruments = conn.execute(text(
            "SELECT count(*) FROM instruments WHERE symbol = ANY(:syms)"),
            {"syms": list(TEST_SYMBOLS)}).scalar_one()
        acct = conn.execute(text(
            "SELECT cash_usd, last_synced_at FROM broker_accounts "
            "WHERE broker='robinhood' AND external_id=:e"), {"e": TEST_ACCOUNT}).one()
    assert n_instruments == 4
    assert acct.cash_usd == Decimal("1234.56")
    assert acct.last_synced_at is not None

    # full-mirror semantics: a position missing from the next payload is deleted
    rows_minus_nvda = [r for r in rows if r.symbol != "NVDA"]
    upsert(pg_engine, account, rows_minus_nvda)
    remaining = _position_rows(pg_engine)
    assert len(remaining) == 3
    assert "NVDA" not in {r.symbol for r in remaining}
