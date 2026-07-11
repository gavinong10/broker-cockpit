"""Baskets internal API: import (with dry-run), list, detail, soft close.

Same conventions as app/portfolio_api.py: internal-auth on every route,
Decimals serialized as strings. Matching/accounting lives in app/baskets.py.
"""
import json

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.baskets import (DuplicateSlug, ManifestError, ValidationConflict,
                         create_basket)
from app.internal_auth import require_internal

router = APIRouter(prefix="/internal/baskets", dependencies=[Depends(require_internal)])

_CENT = Decimal("0.01")
_PCT = Decimal("0.0001")
_ZERO = Decimal("0")

# One row per basket with deployed cost, current value, nearest option expiry.
_BASKET_SUMMARY_SQL = (
    "SELECT b.id, b.slug, b.name, b.thesis, b.source_ref, b.horizon, "
    "b.invalidation, b.status, b.created_at, "
    "COALESCE(SUM(ba.cost_basis_usd), 0) AS deployed_usd, "
    "COALESCE(SUM(ba.qty * COALESCE(px.last_price_usd, 0) "
    "             * COALESCE(i.multiplier, 1)), 0) AS current_value_usd, "
    "MIN(i.expiry) AS nearest_expiry "
    "FROM baskets b "
    "LEFT JOIN basket_allocations ba ON ba.basket_id = b.id "
    "LEFT JOIN instruments i ON i.id = ba.instrument_id "
    "LEFT JOIN LATERAL (SELECT MAX(p.last_price_usd) AS last_price_usd "
    "                   FROM positions p WHERE p.instrument_id = i.id) px ON true "
    "{where}"
    "GROUP BY b.id ")

_ALLOCATION_ROWS_SQL = (
    "SELECT i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", "
    "COALESCE(i.multiplier, 1) AS multiplier, "
    "ba.qty, ba.cost_basis_usd, px.last_price_usd, px.prev_close_usd "
    "FROM basket_allocations ba "
    "JOIN instruments i ON i.id = ba.instrument_id "
    "LEFT JOIN LATERAL (SELECT MAX(p.last_price_usd) AS last_price_usd, "
    "                          MAX(p.prev_close_usd) AS prev_close_usd "
    "                   FROM positions p WHERE p.instrument_id = i.id) px ON true "
    "WHERE ba.basket_id = :b")


def _get_engine():
    from app import main  # deferred: main imports this module at startup
    return main.get_engine()


def _s(value):
    return None if value is None else str(value)


class ImportRequest(BaseModel):
    manifest: dict
    dry_run: bool = False


@router.post("/import")
def import_basket(req: ImportRequest):
    try:
        return create_basket(_get_engine(), req.manifest, dry_run=req.dry_run)
    except ValidationConflict as exc:
        raise HTTPException(status_code=400, detail={
            "error": "over_allocation", "conflicts": exc.conflicts})
    except ManifestError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "manifest", "message": str(exc)})
    except DuplicateSlug as exc:
        raise HTTPException(status_code=409, detail={
            "error": "duplicate_slug", "message": str(exc)})


def _summarize(row) -> dict:
    deployed = Decimal(row.deployed_usd).quantize(_CENT)
    value = Decimal(row.current_value_usd).quantize(_CENT)
    pl = (value - deployed).quantize(_CENT)
    pl_pct = (pl / abs(deployed) * 100).quantize(_PCT) if deployed != 0 else None
    return {
        "slug": row.slug,
        "name": row.name,
        "thesis": row.thesis,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "deployed_usd": str(deployed),
        "current_value_usd": str(value),
        "pl_usd": str(pl),
        "pl_pct": _s(pl_pct),
        "nearest_expiry": row.nearest_expiry.isoformat() if row.nearest_expiry else None,
    }


@router.get("")
def list_baskets():
    with _get_engine().connect() as conn:
        rows = conn.execute(text(
            _BASKET_SUMMARY_SQL.format(where="") + "ORDER BY b.created_at, b.id")).all()
    return [_summarize(r) for r in rows]


def _scoped_position(row, total_value: Decimal) -> dict:
    """Basket-scoped position row: same shape as /internal/portfolio rows."""
    qty = Decimal(row.qty)
    mult = Decimal(row.multiplier)
    last = row.last_price_usd
    mv = (qty * last * mult) if last is not None else _ZERO
    cost = row.cost_basis_usd
    unrealized = (mv - cost) if (last is not None and cost is not None) else _ZERO
    day = ((last - row.prev_close_usd) * qty * mult) \
        if (last is not None and row.prev_close_usd is not None) else _ZERO
    avg_cost = (Decimal(cost) / (qty * mult)) if (cost is not None and qty != 0) else None
    weight = (mv / total_value * 100).quantize(_PCT) if total_value != 0 else _ZERO
    return {
        "symbol": row.symbol,
        "sec_type": row.sec_type,
        "qty": str(qty),
        "avg_cost_usd": _s(avg_cost),
        "last_price_usd": _s(last),
        "prev_close_usd": _s(row.prev_close_usd),
        "market_value_usd": str(mv.quantize(_CENT)),
        "unrealized_pl_usd": str(Decimal(unrealized).quantize(_CENT)),
        "day_change_usd": str(Decimal(day).quantize(_CENT)),
        "weight_pct": str(weight),
        "expiry": row.expiry.isoformat() if row.expiry else None,
        "strike": _s(row.strike),
        "right": row.right,
    }


@router.get("/{slug}")
def basket_detail(slug: str):
    with _get_engine().connect() as conn:
        row = conn.execute(text(_BASKET_SUMMARY_SQL.format(where="WHERE b.slug = :slug ")),
                           {"slug": slug}).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown basket")
        alloc_rows = conn.execute(text(_ALLOCATION_ROWS_SQL), {"b": row.id}).all()
        snap_rows = conn.execute(text(
            "SELECT taken_on, value_usd FROM basket_snapshots "
            "WHERE basket_id = :b ORDER BY taken_on ASC"), {"b": row.id}).all()

    body = _summarize(row)
    body.update({
        "source_ref": row.source_ref,
        "horizon": row.horizon,
        "invalidation": row.invalidation,
    })
    total_value = Decimal(row.current_value_usd)
    positions = [_scoped_position(r, total_value) for r in alloc_rows]
    positions.sort(key=lambda p: Decimal(p["market_value_usd"]), reverse=True)
    body["positions"] = positions
    body["snapshots"] = [{"taken_on": s.taken_on.isoformat(),
                          "value_usd": str(s.value_usd)} for s in snap_rows]
    return body


@router.delete("/{slug}")
def close_basket(slug: str):
    with _get_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id, status FROM baskets WHERE slug = :s"), {"s": slug}).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown basket")
        if row.status != "closed":
            conn.execute(text("UPDATE baskets SET status = 'closed' WHERE id = :i"),
                         {"i": row.id})
            n_alloc = conn.execute(text(
                "SELECT count(*) FROM basket_allocations WHERE basket_id = :i"),
                {"i": row.id}).scalar_one()
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'basket.closed', CAST(:p AS jsonb))"),
                {"p": json.dumps({"slug": slug, "allocations": n_alloc})})
    return {"slug": slug, "status": "closed"}
