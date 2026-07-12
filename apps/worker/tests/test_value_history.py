"""Value-history tests: backfill never overwrites, cash-flow idempotency.

Postgres-gated; seeds one RH account (VH-TEST-RH) and namespaced snapshot
days far in the past (2019) so cleanup can be surgical. Fetchers injected —
no Robinhood session touched.
"""
import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.value_history import backfill_snapshots, sync_cash_flows

ACCOUNT = "VH-TEST-RH"
DAYS = (date(2019, 1, 7), date(2019, 1, 8), date(2019, 1, 9))


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
        conn.execute(text("DELETE FROM snapshots WHERE taken_on <= '2019-12-31'"))
        conn.execute(text(
            "DELETE FROM cash_flows WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE external_id = :e)"), {"e": ACCOUNT})
        conn.execute(text("DELETE FROM broker_accounts WHERE external_id = :e"),
                     {"e": ACCOUNT})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category IN "
            "('snapshot.backfill', 'cashflows.synced')"))


def _seed(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd) "
            "VALUES ('robinhood', :e, 'USD', 0)"), {"e": ACCOUNT})
        # an OBSERVED snapshot for the middle day — backfill must not touch it
        conn.execute(text(
            "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
            "VALUES ('2019-01-08', 999999, 0, '{}')"))


def _hist(day: date, equity: str) -> dict:
    return {"begins_at": f"{day.isoformat()}T14:30:00Z",
            "adjusted_close_equity": equity}


def test_backfill_inserts_only_missing_days(pg_engine):
    items = [_hist(DAYS[0], "100000"), _hist(DAYS[1], "111111"),
             _hist(DAYS[2], "120000"), {"begins_at": "junk"}]
    out = backfill_snapshots(pg_engine, fetch_fn=lambda span: items)
    assert out == {"inserted": 2, "skipped_existing": 1, "skipped_bad": 1}
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT taken_on, total_value_usd, source FROM snapshots "
            "WHERE taken_on <= '2019-12-31' ORDER BY taken_on")).all()
    assert [(r.taken_on, str(r.source)) for r in rows] == [
        (DAYS[0], "backfill_rh"), (DAYS[1], "observed"), (DAYS[2], "backfill_rh")]
    # the observed middle day kept its value
    assert Decimal(rows[1].total_value_usd) == Decimal("999999")
    # idempotent second run inserts nothing
    again = backfill_snapshots(pg_engine, fetch_fn=lambda span: items)
    assert again["inserted"] == 0


def _transfer(tid, direction="deposit", state="completed", amount="1000.00"):
    return {"id": tid, "direction": direction, "state": state, "amount": amount,
            "created_at": "2026-07-01T12:00:00Z"}


def test_cash_flow_sync_idempotent_and_signed(pg_engine):
    transfers = [
        _transfer("t1"),                                    # +1000 deposit
        _transfer("t2", direction="withdraw", amount="250"),  # -250 withdrawal
        _transfer("t3", state="pending"),                   # skipped
        {"id": None, "direction": "deposit", "state": "completed"},  # bad row
    ]
    out = sync_cash_flows(pg_engine, fetch_fn=lambda: transfers)
    assert out["inserted"] == 2 and out["skipped"] == 2
    again = sync_cash_flows(pg_engine, fetch_fn=lambda: transfers)
    assert again["inserted"] == 0                            # source_ref dedupe
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT kind, amount_usd, source_ref FROM cash_flows cf "
            "JOIN broker_accounts a ON a.id = cf.broker_account_id "
            "WHERE a.external_id = :e ORDER BY source_ref"), {"e": ACCOUNT}).all()
    assert [(str(r.kind), Decimal(r.amount_usd)) for r in rows] == [
        ("deposit", Decimal("1000.00")), ("withdrawal", Decimal("-250.00"))]
    assert rows[0].source_ref == "rh-ach:t1"
