"""Baskets tests: schema, allocation matching, accounting, API.

Postgres-gated tests seed one RH account (BSK-TEST-RH) with:
  BSKQ  STK  qty 100 @ avg 50, last 55, prev 54          (mult NULL -> 1)
  BSKQ261218C00060000  OPT qty 2 @ avg 4.00, last 5.00   (x100, exp 2026-12-18)
  BSKQ270115C00065000  OPT qty 3 @ avg 2.00, last 2.50   (x100, exp 2027-01-15)
  BSKQX260918C00010000 OPT qty 1 (prefix trap: underlying BSKQX, not BSKQ)

Seeds are namespaced (BSK-TEST-* / BSKQ* / bsk-test-*) and cleaned up before
and after, mirroring tests/test_snapshots.py hygiene.

Basket "full" (STK 40 + all BSKQ options):
  deployed = 40*50 + 2*4*100 + 3*2*100          = 2000 + 800 + 600 = 3400
  value    = 40*55 + 2*5*100 + 3*2.5*100        = 2200 + 1000 + 750 = 3950
  pl       = 550, pl_pct = 550/3400*100 = 16.1765(ish)
"""
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import settings
from app.main import app

TOKEN_HEADERS = {"X-Internal-Token": settings.internal_api_token}

ACCOUNT = "BSK-TEST-RH"
STK = "BSKQ"
CALL_NEAR = "BSKQ261218C00060000"   # exp 2026-12-18
CALL_FAR = "BSKQ270115C00065000"    # exp 2027-01-15
TRAP = "BSKQX260918C00010000"       # underlying BSKQX -- must never match "BSKQ"
TEST_SYMBOLS = (STK, CALL_NEAR, CALL_FAR, TRAP)

SLUG_A = "bsk-test-alpha"
SLUG_B = "bsk-test-beta"
TEST_SLUGS = (SLUG_A, SLUG_B)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


# --- auth: all basket routes 401 without token (no DB needed) ------------------

def test_all_basket_routes_require_token():
    c = TestClient(app)
    assert c.post("/internal/baskets/import", json={"manifest": {}}).status_code == 401
    assert c.get("/internal/baskets").status_code == 401
    assert c.get(f"/internal/baskets/{SLUG_A}").status_code == 401
    assert c.delete(f"/internal/baskets/{SLUG_A}").status_code == 401


# --- postgres-gated fixtures ----------------------------------------------------

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
            "DELETE FROM basket_snapshots WHERE basket_id IN "
            "(SELECT id FROM baskets WHERE slug = ANY(:s))"), {"s": list(TEST_SLUGS)})
        conn.execute(text(
            "DELETE FROM basket_allocations WHERE basket_id IN "
            "(SELECT id FROM baskets WHERE slug = ANY(:s))"), {"s": list(TEST_SLUGS)})
        conn.execute(text("DELETE FROM baskets WHERE slug = ANY(:s)"),
                     {"s": list(TEST_SLUGS)})
        conn.execute(text(
            "DELETE FROM positions WHERE broker_account_id IN "
            "(SELECT id FROM broker_accounts WHERE external_id = :e)"), {"e": ACCOUNT})
        conn.execute(text("DELETE FROM broker_accounts WHERE external_id = :e"),
                     {"e": ACCOUNT})
        conn.execute(text("DELETE FROM instruments WHERE symbol = ANY(:syms)"),
                     {"syms": list(TEST_SYMBOLS)})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category IN ('basket.created', 'basket.closed') "
            "AND payload->>'slug' = ANY(:s)"), {"s": list(TEST_SLUGS)})
        conn.execute(text("DELETE FROM snapshots WHERE taken_on = :d"), {"d": _today_utc()})


def _seed(eng):
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        acct_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('robinhood', :e, 'USD', 0, :ts) RETURNING id"),
            {"e": ACCOUNT, "ts": now}).scalar_one()
        stk_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency) "
            "VALUES (:s, 'STK', 'USD') RETURNING id"), {"s": STK}).scalar_one()
        near_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', '2026-12-18', 60, 'C', 100) RETURNING id"),
            {"s": CALL_NEAR}).scalar_one()
        far_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', '2027-01-15', 65, 'C', 100) RETURNING id"),
            {"s": CALL_FAR}).scalar_one()
        trap_id = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', '2026-09-18', 10, 'C', 100) RETURNING id"),
            {"s": TRAP}).scalar_one()
        for iid, qty, avg, last, prev in (
                (stk_id, "100", "50", "55", "54"),
                (near_id, "2", "4.00", "5.00", "4.80"),
                (far_id, "3", "2.00", "2.50", "2.40"),
                (trap_id, "1", "1.00", "1.00", "1.00")):
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, "
                "avg_cost_usd, last_price_usd, prev_close_usd) "
                "VALUES (:a, :i, :q, :c, :l, :p)"),
                {"a": acct_id, "i": iid, "q": qty, "c": avg, "l": last, "p": prev})


def _manifest(slug=SLUG_A, legs=None, **extra):
    m = {"slug": slug, "name": f"Basket {slug}", "thesis": "test thesis", "legs": legs or []}
    m.update(extra)
    return m


def _import(client, manifest, dry_run=False):
    return client.post("/internal/baskets/import",
                       json={"manifest": manifest, "dry_run": dry_run},
                       headers=TOKEN_HEADERS)


def _allocs_by_symbol(body):
    return {a["symbol"]: a for a in body["allocations"]}


# --- matching: OPT by underlying -------------------------------------------------

def test_opt_leg_matches_all_expiries_on_underlying(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": "BSKQ", "sec_type": "OPT"}]))
    assert resp.status_code == 200, resp.text
    allocs = _allocs_by_symbol(resp.json())
    assert set(allocs) == {CALL_NEAR, CALL_FAR}  # both expiries, no trap, no STK
    assert Decimal(allocs[CALL_NEAR]["qty"]) == 2
    assert Decimal(allocs[CALL_FAR]["qty"]) == 3
    # cost basis at allocation time: avg_cost * qty * multiplier
    assert Decimal(allocs[CALL_NEAR]["cost_basis_usd"]) == Decimal("800")
    assert Decimal(allocs[CALL_FAR]["cost_basis_usd"]) == Decimal("600")


def test_explicit_occ_leg_matches_only_that_contract(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": CALL_NEAR, "sec_type": "OPT"}]))
    assert resp.status_code == 200, resp.text
    allocs = _allocs_by_symbol(resp.json())
    assert set(allocs) == {CALL_NEAR}
    assert Decimal(allocs[CALL_NEAR]["qty"]) == 2


# --- matching: STK slices ---------------------------------------------------------

def test_stk_leg_requires_explicit_qty(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK"}]))
    assert resp.status_code == 400


def test_stk_qty_slice_and_cost_basis(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "40"}]))
    assert resp.status_code == 200, resp.text
    allocs = _allocs_by_symbol(resp.json())
    assert set(allocs) == {STK}
    assert Decimal(allocs[STK]["qty"]) == 40
    assert Decimal(allocs[STK]["cost_basis_usd"]) == Decimal("2000")  # 40 * 50 * 1


# --- over-allocation across baskets ----------------------------------------------

def test_over_allocation_rejected_with_conflict_detail(client):
    assert _import(client, _manifest(SLUG_A, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "80"}])).status_code == 200
    resp = _import(client, _manifest(SLUG_B, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "40"}]))
    assert resp.status_code == 400
    body = resp.json()
    conflicts = body["detail"]["conflicts"]
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["symbol"] == STK
    assert Decimal(c["requested"]) == 40
    assert Decimal(c["available"]) == 20


def test_second_basket_takes_remaining_qty(client):
    assert _import(client, _manifest(SLUG_A, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "80"}])).status_code == 200
    resp = _import(client, _manifest(SLUG_B, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "20"}]))
    assert resp.status_code == 200, resp.text
    assert Decimal(_allocs_by_symbol(resp.json())[STK]["qty"]) == 20


def test_opt_underlying_fully_allocated_conflicts(client):
    assert _import(client, _manifest(SLUG_A, legs=[
        {"symbol_or_underlying": "BSKQ", "sec_type": "OPT"}])).status_code == 200
    resp = _import(client, _manifest(SLUG_B, legs=[
        {"symbol_or_underlying": "BSKQ", "sec_type": "OPT"}]))
    assert resp.status_code == 400
    assert resp.json()["detail"]["conflicts"]


def test_duplicate_slug_conflict(client):
    m = _manifest(legs=[{"symbol_or_underlying": STK, "sec_type": "STK", "qty": "10"}])
    assert _import(client, m).status_code == 200
    assert _import(client, m).status_code == 409


def test_leg_matching_no_position_rejected(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": "ZZZNOSUCH", "sec_type": "OPT"}]))
    assert resp.status_code == 400
    assert resp.json()["detail"]["conflicts"]


# --- dry run ----------------------------------------------------------------------

def test_dry_run_computes_allocations_but_writes_nothing(client, pg_engine):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": "BSKQ", "sec_type": "OPT"},
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "40"}]), dry_run=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert set(_allocs_by_symbol(body)) == {STK, CALL_NEAR, CALL_FAR}
    with pg_engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM baskets WHERE slug = ANY(:s)"),
                         {"s": list(TEST_SLUGS)}).scalar_one()
    assert n == 0
    assert client.get("/internal/baskets", headers=TOKEN_HEADERS).json() == []


# --- value math + snapshots --------------------------------------------------------

def _create_full_basket(client):
    resp = _import(client, _manifest(legs=[
        {"symbol_or_underlying": "BSKQ", "sec_type": "OPT"},
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "40"}]))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_basket_value_math_includes_multiplier(client, pg_engine):
    from app.baskets import basket_value
    _create_full_basket(client)
    with pg_engine.connect() as conn:
        bid = conn.execute(text("SELECT id FROM baskets WHERE slug = :s"),
                           {"s": SLUG_A}).scalar_one()
    # 40*55*1 + 2*5*100 + 3*2.5*100 = 2200 + 1000 + 750
    assert basket_value(pg_engine, bid) == Decimal("3950.00")


def test_basket_snapshots_idempotent_per_day(client, pg_engine):
    from app.snapshots import record_snapshot
    _create_full_basket(client)
    record_snapshot(pg_engine)
    record_snapshot(pg_engine)  # same day -> upsert, not a second row
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT bs.taken_on, bs.value_usd FROM basket_snapshots bs "
            "JOIN baskets b ON b.id = bs.basket_id WHERE b.slug = :s"),
            {"s": SLUG_A}).all()
    assert len(rows) == 1
    assert rows[0].taken_on == _today_utc()
    assert rows[0].value_usd == Decimal("3950.00")


# --- list / detail shapes -----------------------------------------------------------

def test_list_shape_with_pl_and_nearest_expiry(client):
    _create_full_basket(client)
    resp = client.get("/internal/baskets", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    rows = [b for b in resp.json() if b["slug"] == SLUG_A]
    assert len(rows) == 1
    b = rows[0]
    assert b["name"] == f"Basket {SLUG_A}"
    assert b["thesis"] == "test thesis"
    assert b["status"] == "open"
    assert Decimal(b["deployed_usd"]) == Decimal("3400.00")
    assert Decimal(b["current_value_usd"]) == Decimal("3950.00")
    assert Decimal(b["pl_usd"]) == Decimal("550.00")
    assert abs(Decimal(b["pl_pct"]) - Decimal("550") / Decimal("3400") * 100) < Decimal("0.01")
    assert b["nearest_expiry"] == "2026-12-18"


def test_detail_shape_scoped_positions_and_snapshots(client, pg_engine):
    from app.snapshots import record_snapshot
    _create_full_basket(client)
    record_snapshot(pg_engine)
    resp = client.get(f"/internal/baskets/{SLUG_A}", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == SLUG_A
    assert body["status"] == "open"
    positions = {p["symbol"]: p for p in body["positions"]}
    assert set(positions) == {STK, CALL_NEAR, CALL_FAR}
    stk = positions[STK]
    assert stk["sec_type"] == "STK"
    assert Decimal(stk["qty"]) == 40                                  # scoped, not 100
    assert Decimal(stk["market_value_usd"]) == Decimal("2200.00")     # 40 * 55
    assert Decimal(stk["unrealized_pl_usd"]) == Decimal("200.00")     # 2200 - 2000
    near = positions[CALL_NEAR]
    assert near["expiry"] == "2026-12-18"
    assert Decimal(near["market_value_usd"]) == Decimal("1000.00")    # 2 * 5 * 100
    snaps = body["snapshots"]
    assert len(snaps) >= 1
    dates = [s["taken_on"] for s in snaps]
    assert dates == sorted(dates)  # ascending
    assert Decimal(snaps[-1]["value_usd"]) == Decimal("3950.00")


def test_detail_unknown_slug_404(client):
    assert client.get("/internal/baskets/zzz-no-such", headers=TOKEN_HEADERS).status_code == 404


# --- portfolio chips ------------------------------------------------------------------

def test_portfolio_rows_carry_basket_chips(client):
    _create_full_basket(client)
    resp = client.get("/internal/portfolio", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    positions = {p["symbol"]: p for p in resp.json()["positions"]}
    stk_chips = positions[STK]["baskets"]
    assert stk_chips == [{"slug": SLUG_A, "qty": "40.00000000"}] or \
        (len(stk_chips) == 1 and stk_chips[0]["slug"] == SLUG_A
         and Decimal(stk_chips[0]["qty"]) == 40)
    assert positions[TRAP]["baskets"] == []  # unallocated = core


# --- close (soft delete) ---------------------------------------------------------------

def test_close_keeps_history_and_frees_allocations(client, pg_engine):
    assert _import(client, _manifest(SLUG_A, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "80"}])).status_code == 200
    resp = client.delete(f"/internal/baskets/{SLUG_A}", headers=TOKEN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    detail = client.get(f"/internal/baskets/{SLUG_A}", headers=TOKEN_HEADERS)
    assert detail.status_code == 200          # history kept
    assert detail.json()["status"] == "closed"
    # closed basket's qty is freed: a new basket can claim the full 100
    resp = _import(client, _manifest(SLUG_B, legs=[
        {"symbol_or_underlying": STK, "sec_type": "STK", "qty": "100"}]))
    assert resp.status_code == 200, resp.text
    # audit trail
    with pg_engine.connect() as conn:
        created = conn.execute(text(
            "SELECT count(*) FROM audit_log WHERE category = 'basket.created' "
            "AND payload->>'slug' = :s"), {"s": SLUG_A}).scalar_one()
        closed = conn.execute(text(
            "SELECT count(*) FROM audit_log WHERE category = 'basket.closed' "
            "AND payload->>'slug' = :s"), {"s": SLUG_A}).scalar_one()
    assert created == 1
    assert closed == 1


def test_delete_unknown_slug_404(client):
    assert client.delete("/internal/baskets/zzz-no-such", headers=TOKEN_HEADERS).status_code == 404
