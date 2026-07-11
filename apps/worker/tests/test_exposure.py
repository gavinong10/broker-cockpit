import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs postgres")

TOKEN = {"X-Internal-Token": "dev-token"}


@pytest.fixture()
def client():
    from app import main
    eng = create_engine(os.environ["TEST_DATABASE_URL"])
    _cleanup(eng)
    _seed(eng)
    old = main._engine
    main._engine = eng
    try:
        yield TestClient(main.app)
    finally:
        main._engine = old
        _cleanup(eng)
        eng.dispose()


def _cleanup(eng):
    with eng.begin() as c:
        c.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE external_id LIKE 'EXPO-TEST%')"))
        c.execute(text("DELETE FROM instruments WHERE symbol LIKE 'EXPQ%'"))
        c.execute(text("DELETE FROM broker_accounts WHERE external_id LIKE 'EXPO-TEST%'"))


def _seed(eng):
    with eng.begin() as c:
        acct = c.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd) "
            "VALUES ('robinhood', 'EXPO-TEST-1', 'USD', 0) RETURNING id")).scalar()
        stk = c.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency) "
            "VALUES ('EXPQ', 'STK', 'USD') RETURNING id")).scalar()
        call = c.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES ('EXPQ261218C00010000', 'OPT', 'USD', '2026-12-18', 10, 'C', 100) RETURNING id")).scalar()
        short_put = c.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES ('EXPQ261218P00008000', 'OPT', 'USD', '2026-12-18', 8, 'P', 100) RETURNING id")).scalar()
        other = c.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency) "
            "VALUES ('EXPQZ', 'STK', 'USD') RETURNING id")).scalar()
        for inst, qty, price in ((stk, 100, "10.00"), (call, 5, "2.00"),
                                 (short_put, -2, "1.00"), (other, 10, "50.00")):
            c.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, last_price_usd) "
                "VALUES (:a, :i, :q, :p)"), {"a": acct, "i": inst, "q": qty, "p": price})


def test_exposure_groups_options_under_underlying(client):
    r = client.get("/internal/exposure", headers=TOKEN)
    assert r.status_code == 200
    by = {e["underlying"]: e for e in r.json() if e["underlying"].startswith("EXPQ")}
    # EXPQ: 100*10 stock + (5*2*100 long call) + (-2*1*100 short put) = 1000 + 1000 - 200
    e = by["EXPQ"]
    assert Decimal(e["stock_value_usd"]) == Decimal("1000.00")
    assert Decimal(e["option_value_usd"]) == Decimal("800.00")
    assert Decimal(e["total_usd"]) == Decimal("1800.00")
    # EXPQZ must NOT absorb EXPQ options (exact underlying parse) and vice versa
    z = by["EXPQZ"]
    assert Decimal(z["option_value_usd"]) == Decimal("0.00")
    assert Decimal(z["total_usd"]) == Decimal("500.00")
    # sorted by |total| desc within our seeds
    ours = [e2 for e2 in r.json() if e2["underlying"].startswith("EXPQ")]
    assert ours == sorted(ours, key=lambda x: abs(Decimal(x["total_usd"])), reverse=True)


def test_exposure_requires_token(client):
    assert client.get("/internal/exposure").status_code == 401
