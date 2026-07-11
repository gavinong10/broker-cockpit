"""Plan tests: manifest validation, create/list API, duplicate handling.

Postgres-gated tests seed one basket (pln-test-alpha) with no allocations —
plans are pure intent and need no positions. Seeds are namespaced (pln-test-*)
and cleaned before/after, mirroring tests/test_baskets.py hygiene.
"""
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import settings
from app.main import app
from app.plans import (PlanManifestError, planned_total_usd,
                       structure_multiplier, validate_plan_manifest)

TOKEN_HEADERS = {"X-Internal-Token": settings.internal_api_token}

SLUG = "pln-test-alpha"
PLAN_ONLY_SLUG = "pln-test-planonly"

VERTICAL = [
    {"occ": "PLNQ281215C00220000", "sec_type": "OPT", "ratio": 1},
    {"occ": "PLNQ281215C00330000", "sec_type": "OPT", "ratio": -1},
]


def _leg(label="PLNQ Dec-28 220/330", structure=None, **over):
    leg = {
        "label": label,
        "structure": VERTICAL if structure is None else structure,
        "qty": "2",
        "planned_net_debit": "17.23",
    }
    leg.update(over)
    return leg


# --- pure validation (no DB) ----------------------------------------------------

def test_validate_happy_path_defaults():
    legs = validate_plan_manifest({"legs": [_leg()]})
    assert legs[0]["tolerance_pct"] == Decimal("5")
    assert legs[0]["qty"] == Decimal("2")
    assert structure_multiplier(legs[0]["structure"]) == 100
    # 2 units x 17.23/share x 100 = 3446.00
    assert planned_total_usd(legs) == Decimal("3446.00")


def test_validate_stk_structure_multiplier_is_one():
    legs = validate_plan_manifest({"legs": [_leg(
        structure=[{"symbol": "PLNQ", "sec_type": "STK", "ratio": 1}],
        qty="10", planned_net_debit="55")]})
    assert structure_multiplier(legs[0]["structure"]) == 1
    assert planned_total_usd(legs) == Decimal("550.00")


@pytest.mark.parametrize("bad, msg_part", [
    ({}, "legs"),
    ({"legs": []}, "legs"),
    ({"legs": [_leg(label="")]}, "label"),
    ({"legs": [_leg(structure=[])]}, "structure"),
    ({"legs": [_leg(structure=[{"occ": "PLNQ281215C00220000", "sec_type": "OPT",
                                "ratio": 0}])]}, "ratio"),
    ({"legs": [_leg(structure=[{"occ": "PLNQ281215C00220000", "sec_type": "OPT",
                                "ratio": -1}])]}, "positive"),
    ({"legs": [_leg(structure=[{"occ": "not-an-occ", "sec_type": "OPT",
                                "ratio": 1}])]}, "OCC"),
    ({"legs": [_leg(structure=[{"symbol": "bad symbol", "sec_type": "STK",
                                "ratio": 1}])]}, "symbol"),
    ({"legs": [_leg(structure=[{"occ": "PLNQ281215C00220000", "sec_type": "FUT",
                                "ratio": 1}])]}, "sec_type"),
    ({"legs": [_leg(qty="0")]}, "qty"),
    ({"legs": [_leg(planned_net_debit="-1")]}, "planned_net_debit"),
    ({"legs": [_leg(tolerance_pct="101")]}, "tolerance_pct"),
    ({"legs": [_leg(), _leg()]}, "duplicate label"),
])
def test_validate_rejects(bad, msg_part):
    with pytest.raises(PlanManifestError) as exc:
        validate_plan_manifest(bad)
    assert msg_part.lower() in str(exc.value).lower()


# --- auth: plan routes 401 without token (no DB needed) --------------------------

def test_plan_routes_require_token():
    c = TestClient(app)
    assert c.post(f"/internal/baskets/{SLUG}/plan",
                  json={"manifest": {}}).status_code == 401
    assert c.get(f"/internal/baskets/{SLUG}/plan").status_code == 401


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
    import app.main as main
    prev = main._engine
    main._engine = pg_engine
    yield TestClient(app)
    main._engine = prev


def _cleanup(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM basket_plan_marks WHERE plan_leg_id IN "
            "(SELECT pl.id FROM basket_plan_legs pl "
            " JOIN baskets b ON b.id = pl.basket_id WHERE b.slug = :s)"), {"s": SLUG})
        conn.execute(text(
            "DELETE FROM basket_plan_legs WHERE basket_id IN "
            "(SELECT id FROM baskets WHERE slug = :s)"), {"s": SLUG})
        conn.execute(text("DELETE FROM baskets WHERE slug IN (:s, :p)"),
                     {"s": SLUG, "p": PLAN_ONLY_SLUG})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category = 'basket.created' "
            "AND payload->>'slug' = :p"), {"p": PLAN_ONLY_SLUG})
        conn.execute(text(
            "DELETE FROM audit_log WHERE category = 'basket.plan_created' "
            "AND payload->>'slug' = :s"), {"s": SLUG})


def _seed(eng):
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO baskets (slug, name, thesis) "
            "VALUES (:s, 'Plan test basket', 'test thesis')"), {"s": SLUG})


# --- API behaviour ----------------------------------------------------------------

def test_create_and_list_plan(client):
    resp = client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS, json={
        "manifest": {"legs": [
            _leg(),
            _leg(label="PLNQ stock starter",
                 structure=[{"symbol": "PLNQ", "sec_type": "STK", "ratio": 1}],
                 qty="10", planned_net_debit="55", tolerance_pct="2.5",
                 breakeven_underlying="57.5", max_value_usd="1000",
                 thesis_note="starter position"),
        ]}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    # 2 x 17.23 x 100 + 10 x 55 x 1 = 3446 + 550 = 3996.00
    assert body["planned_total_usd"] == "3996.00"

    listing = client.get(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS).json()
    assert listing["slug"] == SLUG
    assert [leg["label"] for leg in listing["legs"]] == [
        "PLNQ Dec-28 220/330", "PLNQ stock starter"]
    vertical = listing["legs"][0]
    assert vertical["status"] == "pending"
    assert vertical["monitor_status"] is None
    assert Decimal(vertical["tolerance_pct"]) == Decimal("5")   # default applied
    assert vertical["structure"][1]["ratio"] == -1   # short leg survived round-trip
    stock = listing["legs"][1]
    assert Decimal(stock["tolerance_pct"]) == Decimal("2.5")
    assert Decimal(stock["breakeven_underlying"]) == Decimal("57.5")
    assert stock["thesis_note"] == "starter position"


def test_duplicate_label_conflicts_409(client):
    first = client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                        json={"manifest": {"legs": [_leg()]}})
    assert first.status_code == 200
    again = client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                        json={"manifest": {"legs": [_leg()]}})
    assert again.status_code == 409
    assert again.json()["detail"]["labels"] == ["PLNQ Dec-28 220/330"]
    # conflict rejected the whole payload: still exactly one leg
    listing = client.get(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS).json()
    assert len(listing["legs"]) == 1


def test_manifest_error_400(client):
    resp = client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                       json={"manifest": {"legs": [_leg(qty="-3")]}})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "plan_manifest"


def test_unknown_basket_404(client):
    for method, kwargs in (("post", {"json": {"manifest": {"legs": [_leg()]}}}),
                           ("get", {})):
        resp = getattr(client, method)(
            "/internal/baskets/pln-test-nope/plan", headers=TOKEN_HEADERS, **kwargs)
        assert resp.status_code == 404


def test_audit_row_written(client, pg_engine):
    client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                json={"manifest": {"legs": [_leg()]}})
    with pg_engine.connect() as conn:
        payload = conn.execute(text(
            "SELECT payload FROM audit_log WHERE category = 'basket.plan_created' "
            "AND payload->>'slug' = :s ORDER BY id DESC LIMIT 1"), {"s": SLUG}).scalar_one()
    assert payload["legs"] == 1
    assert payload["planned_total_usd"] == "3446.00"


# --- Task 5: plan view (marks + payoff curve) --------------------------------------

def test_plan_view_marks_and_curve(client, pg_engine):
    client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                json={"manifest": {"legs": [_leg()]}})
    # simulate two monitor cycles' marks with a spot anchor at 220
    with pg_engine.begin() as conn:
        leg_id = conn.execute(text(
            "SELECT pl.id FROM basket_plan_legs pl JOIN baskets b ON b.id = pl.basket_id "
            "WHERE b.slug = :s"), {"s": SLUG}).scalar_one()
        for net, spot in (("18.10", "215.0"), ("16.90", "220.0")):
            conn.execute(text(
                "INSERT INTO basket_plan_marks (plan_leg_id, net_cost, underlying_spot, "
                "quote_basis) VALUES (:l, :n, :s, 'mid')"), {"l": leg_id, "n": net, "s": spot})
        conn.execute(text(
            "UPDATE basket_plan_legs SET last_quote_net = '16.90', "
            "monitor_status = 'in_window' WHERE id = :l"), {"l": leg_id})

    view = client.get(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS).json()
    leg = view["legs"][0]
    assert len(leg["marks"]) == 2
    assert Decimal(leg["marks"][-1]["underlying_spot"]) == Decimal("220")
    # delta: 16.90 / 17.23 - 1 = -1.9%
    assert leg["last_quote_delta_pct"] == "-1.9"
    assert view["planned_total_usd"] == "3446.00"      # 2 x 17.23 x 100
    assert view["curve_excluded"] == []

    curve = {p["move_pct"]: Decimal(p["pnl_usd"]) for p in view["payoff_curve"]}
    # spot anchor 220, strikes 220/330 (width 110), qty 2, planned 17.23:
    # at -50%: intrinsic 0        -> pnl = -3446.00
    # at 0%:   intrinsic 0 (ATM)  -> pnl = -3446.00
    # at +50%: S=330, intrinsic (330-220)=110 capped -> 2*100*(110-17.23) = 18554.00
    # at +100%: capped at width   -> same 18554.00
    assert curve["-50"] == Decimal("-3446.00")
    assert curve["0"] == Decimal("-3446.00")
    assert curve["50"] == Decimal("18554.00")
    assert curve["100"] == Decimal("18554.00")


def test_plan_view_curve_excluded_without_spot_anchor(client):
    client.post(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS,
                json={"manifest": {"legs": [_leg()]}})
    view = client.get(f"/internal/baskets/{SLUG}/plan", headers=TOKEN_HEADERS).json()
    assert view["payoff_curve"] == []
    assert view["curve_excluded"] == ["PLNQ Dec-28 220/330"]


def test_plan_only_basket_import_with_empty_legs(client):
    """A basket may be created with zero allocation legs — pure pending intent
    (the plan block carries the structures until fills graduate)."""
    resp = client.post("/internal/baskets/import", headers=TOKEN_HEADERS, json={
        "manifest": {"slug": "pln-test-planonly", "name": "Plan only",
                     "thesis": "pending intent", "legs": []}})
    assert resp.status_code == 200, resp.text
    detail = client.get("/internal/baskets/pln-test-planonly",
                        headers=TOKEN_HEADERS).json()
    assert detail["positions"] == []
    assert detail["deployed_usd"] == "0.00"
