"""IBKR position sync — fully mock-tested (no network, no gateway).

Fake objects mirror ib_async's real shapes (checked against .venv source):
- ``PortfolioItem`` is a NamedTuple: contract, position, marketPrice,
  marketValue, averageCost, unrealizedPNL, realizedPNL, account
  (ib_async/objects.py:302).
- ``Contract`` is a dataclass with conId, symbol, secType, strike (float),
  right ('C'/'P'), multiplier (STRING), currency, and
  lastTradeDateOrContractMonth as 'YYYYMMDD' (ib_async/contract.py).
- ``AccountValue`` is a NamedTuple: account, tag, value, currency, modelCode
  (ib_async/objects.py:218).

Critical semantic: TWS reports ``averageCost`` for options PER CONTRACT —
it already includes the multiplier — while for stocks it is per share.
ib_async passes the raw value straight through (decoder.py:249).
"""
import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import NamedTuple

import pytest
from sqlalchemy import create_engine, text

from app.ibkr_sync import extract_cash, normalize_items, run, upsert_ibkr
from app.robinhood import AccountInfo, PositionRow, occ_symbol, upsert as rh_upsert

TEST_ACCOUNT = "DU1234567"
TEST_RH_ACCOUNT = "5RHIBKRTEST"
TEST_SYMBOLS = ("AAPL", "NVDA", "AAPL261218C00150000", "NVDA260821P00115000", "MSFT")


# --- fakes mirroring ib_async shapes ---------------------------------------

@dataclass
class FakeContract:
    conId: int = 0
    symbol: str = ""
    secType: str = ""
    lastTradeDateOrContractMonth: str = ""
    strike: float = 0.0
    right: str = ""
    multiplier: str = ""
    currency: str = "USD"


class FakeItem(NamedTuple):
    contract: FakeContract
    position: float
    marketPrice: float
    marketValue: float
    averageCost: float
    unrealizedPNL: float
    realizedPNL: float
    account: str


class FakeAccountValue(NamedTuple):
    account: str
    tag: str
    value: str
    currency: str
    modelCode: str = ""


def stk_item(symbol="AAPL", con_id=265598, position=12.5, price=195.12,
             avg_cost=187.23, account=TEST_ACCOUNT) -> FakeItem:
    return FakeItem(
        contract=FakeContract(conId=con_id, symbol=symbol, secType="STK",
                              multiplier="", currency="USD"),
        position=position, marketPrice=price, marketValue=position * price,
        averageCost=avg_cost, unrealizedPNL=0.0, realizedPNL=0.0, account=account)


def opt_item(underlying="AAPL", con_id=700123456, expiry="20261218",
             strike=150.0, right="C", position=2.0, price=48.15,
             avg_cost_per_contract=512.50, account=TEST_ACCOUNT) -> FakeItem:
    return FakeItem(
        contract=FakeContract(conId=con_id, symbol=underlying, secType="OPT",
                              lastTradeDateOrContractMonth=expiry, strike=strike,
                              right=right, multiplier="100", currency="USD"),
        position=position, marketPrice=price, marketValue=position * price * 100,
        averageCost=avg_cost_per_contract, unrealizedPNL=0.0, realizedPNL=0.0,
        account=account)


# --- normalization -----------------------------------------------------------

def test_normalize_stk():
    rows = normalize_items([stk_item()])
    assert len(rows) == 1
    r = rows[0]
    assert isinstance(r, PositionRow)  # reuses the RH row shape
    assert r.symbol == "AAPL"
    assert r.sec_type == "STK"
    assert r.qty == Decimal("12.5")  # fractional shares preserved as Decimal
    # STK averageCost is already per share — stored as-is
    assert r.avg_cost_usd == Decimal("187.23")
    assert r.last_price_usd == Decimal("195.12")
    assert r.prev_close_usd is None  # portfolio items carry no prev close
    assert r.expiry is None and r.strike is None and r.right is None
    assert r.con_id == 265598


def test_normalize_opt():
    rows = normalize_items([opt_item()])
    assert len(rows) == 1
    r = rows[0]
    expiry = date(2026, 12, 18)  # parsed from 'YYYYMMDD'
    # symbol built with the shared occ_symbol helper, not a local copy
    assert r.symbol == occ_symbol("AAPL", expiry, "C", Decimal("150"))
    assert r.symbol == "AAPL261218C00150000"
    assert r.sec_type == "OPT"
    assert r.qty == Decimal("2")
    # IMPORTANT: TWS averageCost for OPT includes the multiplier (per
    # contract) — must be divided down to per-share to match RH semantics.
    assert r.avg_cost_usd == Decimal("512.5") / 100
    assert r.avg_cost_usd == Decimal("5.125")
    assert r.last_price_usd == Decimal("48.15")
    assert r.expiry == expiry
    assert r.strike == Decimal("150")
    assert r.right == "C"
    assert r.multiplier == 100  # multiplier string '100' -> int
    assert r.con_id == 700123456


def test_normalize_short_option_negative_qty():
    item = opt_item(underlying="NVDA", con_id=700999888, expiry="20260821",
                    strike=115.0, right="P", position=-1.0, price=2.05,
                    avg_cost_per_contract=230.0)
    rows = normalize_items([item])
    r = rows[0]
    assert r.qty == Decimal("-1")  # short position sign preserved
    assert r.symbol == "NVDA260821P00115000"
    assert r.right == "P"
    assert r.avg_cost_usd == Decimal("2.30")  # 230.00 per contract / 100


def test_normalize_skips_zero_positions():
    rows = normalize_items([stk_item(position=0.0)])
    assert rows == []


# --- cash extraction ---------------------------------------------------------

def test_extract_cash():
    summary = [
        FakeAccountValue(TEST_ACCOUNT, "NetLiquidation", "99999.99", "USD"),
        FakeAccountValue(TEST_ACCOUNT, "TotalCashValue", "2500.10", "USD"),
        FakeAccountValue(TEST_ACCOUNT, "TotalCashValue", "42.00", "EUR"),
    ]
    assert extract_cash(summary) == Decimal("2500.10")


def test_extract_cash_missing_raises():
    with pytest.raises(ValueError):
        extract_cash([FakeAccountValue(TEST_ACCOUNT, "NetLiquidation", "1", "USD")])


# --- postgres-backed upsert (skipped without TEST_DATABASE_URL) --------------

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
            "(SELECT id FROM broker_accounts WHERE external_id IN (:a, :b))"),
            {"a": TEST_ACCOUNT, "b": TEST_RH_ACCOUNT})
        conn.execute(text(
            "DELETE FROM broker_accounts WHERE external_id IN (:a, :b)"),
            {"a": TEST_ACCOUNT, "b": TEST_RH_ACCOUNT})
        conn.execute(text("DELETE FROM instruments WHERE symbol = ANY(:syms)"),
                     {"syms": list(TEST_SYMBOLS)})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category LIKE 'sync.ibkr.%' "
            "AND payload::text LIKE :acct"), {"acct": f"%{TEST_ACCOUNT}%"})


def _position_rows(eng, broker, external_id):
    with eng.connect() as conn:
        return conn.execute(text(
            "SELECT i.symbol, p.qty, p.avg_cost_usd, i.con_id "
            "FROM positions p "
            "JOIN broker_accounts a ON a.id = p.broker_account_id "
            "JOIN instruments i ON i.id = p.instrument_id "
            "WHERE a.broker = :broker AND a.external_id = :e ORDER BY i.symbol"),
            {"broker": broker, "e": external_id}).all()


def test_upsert_idempotent_full_mirror_and_rh_isolation(pg_engine):
    # seed a ROBINHOOD row first — an ibkr sync must never touch it
    rh_account = AccountInfo(external_id=TEST_RH_ACCOUNT, cash_usd=Decimal("10"))
    rh_rows = [PositionRow(symbol="MSFT", sec_type="STK", qty=Decimal("3"),
                           avg_cost_usd=Decimal("400"), last_price_usd=Decimal("410"),
                           prev_close_usd=None)]
    rh_upsert(pg_engine, rh_account, rh_rows)

    rows = normalize_items([
        stk_item(),
        opt_item(),
        opt_item(underlying="NVDA", con_id=700999888, expiry="20260821",
                 strike=115.0, right="P", position=-1.0, price=2.05,
                 avg_cost_per_contract=230.0),
    ])
    cash = Decimal("2500.10")

    upsert_ibkr(pg_engine, TEST_ACCOUNT, rows, cash)
    first = _position_rows(pg_engine, "ibkr", TEST_ACCOUNT)
    assert len(first) == 3
    by_symbol = {r.symbol: r for r in first}
    assert by_symbol["AAPL"].con_id == 265598  # conId lands on instruments.con_id
    assert by_symbol["AAPL261218C00150000"].con_id == 700123456
    assert by_symbol["NVDA260821P00115000"].qty == Decimal("-1")

    # idempotent: second run, same rows, no duplicates
    upsert_ibkr(pg_engine, TEST_ACCOUNT, rows, cash)
    assert _position_rows(pg_engine, "ibkr", TEST_ACCOUNT) == first
    with pg_engine.connect() as conn:
        n_instruments = conn.execute(text(
            "SELECT count(*) FROM instruments WHERE symbol = ANY(:syms)"),
            {"syms": list(TEST_SYMBOLS)}).scalar_one()
        acct = conn.execute(text(
            "SELECT cash_usd FROM broker_accounts "
            "WHERE broker='ibkr' AND external_id=:e"), {"e": TEST_ACCOUNT}).one()
    assert n_instruments == 4  # 3 ibkr + 1 rh (MSFT)
    assert acct.cash_usd == Decimal("2500.10")

    # full-mirror: dropping the AAPL call deletes its position row
    upsert_ibkr(pg_engine, TEST_ACCOUNT, [r for r in rows if r.right != "C"], cash)
    remaining = _position_rows(pg_engine, "ibkr", TEST_ACCOUNT)
    assert {r.symbol for r in remaining} == {"AAPL", "NVDA260821P00115000"}

    # the ROBINHOOD account's rows survived every ibkr sync untouched
    rh_after = _position_rows(pg_engine, "robinhood", TEST_RH_ACCOUNT)
    assert len(rh_after) == 1
    assert rh_after[0].symbol == "MSFT" and rh_after[0].qty == Decimal("3")


def test_upsert_matches_existing_instrument_by_con_id(pg_engine):
    # same contract, symbol drifted (e.g. corporate action) -> con_id wins, no dup
    upsert_ibkr(pg_engine, TEST_ACCOUNT, normalize_items([stk_item()]), Decimal("1"))
    upsert_ibkr(pg_engine, TEST_ACCOUNT,
                normalize_items([stk_item(symbol="NVDA")]), Decimal("1"))
    with pg_engine.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM instruments WHERE con_id = 265598")).scalar_one()
    assert n == 1


class FakeIB:
    def __init__(self, items, summary):
        self._items = items
        self._summary = summary

    def portfolio(self, account: str = ""):
        return [i for i in self._items if not account or i.account == account]

    async def accountSummaryAsync(self, account: str = ""):
        return [v for v in self._summary if not account or v.account == account]


def test_run_groups_by_account_and_audits(pg_engine):
    ib = FakeIB(
        items=[stk_item(), opt_item()],
        summary=[FakeAccountValue(TEST_ACCOUNT, "TotalCashValue", "2500.10", "USD")],
    )
    asyncio.run(run(pg_engine, ib))

    assert len(_position_rows(pg_engine, "ibkr", TEST_ACCOUNT)) == 2
    with pg_engine.connect() as conn:
        payload = conn.execute(text(
            "SELECT payload FROM audit_log WHERE category = 'sync.ibkr.ok' "
            "ORDER BY id DESC LIMIT 1")).scalar_one()
    assert TEST_ACCOUNT in payload["accounts"]
    assert payload["equity_positions"] == 1
    assert payload["option_positions"] == 1
