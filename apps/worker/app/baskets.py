"""Baskets: named sub-portfolios built from qty slices of synced positions.

A basket allocates quantity slices of existing instruments; `core` is implicit
(any unallocated quantity). Matching rules (see the baskets plan):

- OPT legs name an underlying and match ALL option positions on it, where the
  underlying is the leading alpha prefix of the OCC symbol — unless the leg
  gives a full OCC symbol (contains a digit), which matches only that contract.
- STK legs require an explicit qty (a slice of the stock position).
- Allocation qty is capped by the unallocated remainder across OTHER open
  baskets; explicit requests beyond the remainder raise ValidationConflict
  (mapped to 400 with the conflict list by the API layer).
- cost_basis_usd is captured at allocation time:
  avg_cost_usd x qty x COALESCE(multiplier, 1).

Baskets never place orders — they only label existing synced positions.
"""
import json
import re
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

_CENT = Decimal("0.01")
_ZERO = Decimal("0")

_UNDERLYING_RE = re.compile(r"^([A-Za-z]+)")


class BasketError(Exception):
    """Base for basket domain errors."""


class ManifestError(BasketError):
    """Malformed manifest (missing fields, bad leg spec). -> 400"""


class DuplicateSlug(BasketError):
    """A basket with this slug already exists. -> 409"""


class ValidationConflict(BasketError):
    """One or more legs over-allocate or match nothing. -> 400 with details."""

    def __init__(self, conflicts: list[dict]):
        super().__init__(f"{len(conflicts)} allocation conflict(s)")
        self.conflicts = conflicts


def parse_underlying(occ_symbol: str) -> str:
    """Underlying = the OCC symbol's leading alpha prefix (BSKQ261218C... -> BSKQ)."""
    m = _UNDERLYING_RE.match(occ_symbol)
    return m.group(1) if m else occ_symbol


def _is_full_occ(symbol: str) -> bool:
    return any(ch.isdigit() for ch in symbol)


def _dec(value, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except Exception as exc:
        raise ManifestError(f"leg qty is not a number: {value!r} ({field})") from exc
    if d <= 0:
        raise ManifestError(f"leg qty must be positive: {value!r} ({field})")
    return d


def _validate_manifest(manifest: dict) -> list[dict]:
    if not isinstance(manifest, dict):
        raise ManifestError("manifest must be an object")
    for field in ("slug", "name", "thesis"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ManifestError(f"manifest.{field} is required")
    legs = manifest.get("legs")
    if not isinstance(legs, list) or not legs:
        raise ManifestError("manifest.legs must be a non-empty list")
    for leg in legs:
        if not isinstance(leg, dict) or not leg.get("symbol_or_underlying"):
            raise ManifestError("each leg needs symbol_or_underlying")
        if leg.get("sec_type") not in ("OPT", "STK"):
            raise ManifestError("each leg needs sec_type OPT or STK")
        if leg["sec_type"] == "STK" and leg.get("qty") is None:
            raise ManifestError(
                f"STK leg {leg['symbol_or_underlying']} requires an explicit qty")
    return legs


def _position_aggregates(conn) -> list:
    """One row per held instrument: total qty + weighted avg cost + multiplier."""
    return conn.execute(text(
        "SELECT i.id, i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", "
        "COALESCE(i.multiplier, 1) AS multiplier, "
        "SUM(p.qty) AS qty, "
        "SUM(p.qty * p.avg_cost_usd) FILTER (WHERE p.avg_cost_usd IS NOT NULL) AS cost_notional, "
        "SUM(p.qty) FILTER (WHERE p.avg_cost_usd IS NOT NULL) AS cost_qty "
        "FROM positions p JOIN instruments i ON i.id = p.instrument_id "
        "GROUP BY i.id, i.symbol, i.sec_type, i.expiry, i.strike, i.\"right\", i.multiplier")
    ).all()


def _allocated_by_instrument(conn) -> dict[int, Decimal]:
    """Qty already claimed per instrument across OPEN baskets (signed)."""
    rows = conn.execute(text(
        "SELECT ba.instrument_id, SUM(ba.qty) AS qty "
        "FROM basket_allocations ba JOIN baskets b ON b.id = ba.basket_id "
        "WHERE b.status = 'open' GROUP BY ba.instrument_id")).all()
    return {r.instrument_id: Decimal(r.qty) for r in rows}


def _match_leg(leg: dict, holdings: list) -> list:
    sym = leg["symbol_or_underlying"]
    if leg["sec_type"] == "STK":
        return [h for h in holdings if h.sec_type == "STK" and h.symbol == sym]
    if _is_full_occ(sym):
        return [h for h in holdings if h.sec_type == "OPT" and h.symbol == sym]
    return [h for h in holdings
            if h.sec_type == "OPT" and parse_underlying(h.symbol) == sym]


def _compute_allocations(conn, legs: list[dict]) -> list[dict]:
    """Match legs to holdings, cap by unallocated remainder, capture cost basis.

    Raises ValidationConflict listing every failing leg (over-allocation or
    no matching/unallocated position).
    """
    holdings = _position_aggregates(conn)
    claimed = _allocated_by_instrument(conn)  # other open baskets
    pending: dict[int, Decimal] = {}          # this manifest, prior legs
    allocations: list[dict] = []
    conflicts: list[dict] = []

    for leg in legs:
        matches = _match_leg(leg, holdings)
        if not matches:
            conflicts.append({
                "leg": leg["symbol_or_underlying"], "symbol": None,
                "requested": leg.get("qty"), "available": "0",
                "reason": "no_matching_position"})
            continue
        requested = _dec(leg["qty"], leg["symbol_or_underlying"]) \
            if leg.get("qty") is not None else None
        leg_allocated = False
        for h in matches:
            total = Decimal(h.qty)
            sign = Decimal(-1) if total < 0 else Decimal(1)
            taken = claimed.get(h.id, _ZERO) + pending.get(h.id, _ZERO)
            available = abs(total) - abs(taken)
            if available < 0:
                available = _ZERO
            if requested is not None and requested > available:
                conflicts.append({
                    "leg": leg["symbol_or_underlying"], "symbol": h.symbol,
                    "requested": str(requested), "available": str(available),
                    "reason": "over_allocation"})
                continue
            magnitude = requested if requested is not None else available
            if magnitude == 0:
                continue  # nothing left on this contract; other matches may still allocate
            qty = sign * magnitude
            avg_cost = (Decimal(h.cost_notional) / Decimal(h.cost_qty)
                        if h.cost_qty not in (None, 0) else None)
            cost_basis = (avg_cost * qty * Decimal(h.multiplier)).quantize(
                Decimal("0.0001")) if avg_cost is not None else None
            allocations.append({
                "instrument_id": h.id, "symbol": h.symbol, "sec_type": h.sec_type,
                "qty": qty, "cost_basis_usd": cost_basis,
                "expiry": h.expiry, "strike": h.strike, "right": h.right})
            pending[h.id] = pending.get(h.id, _ZERO) + qty
            leg_allocated = True
        if not leg_allocated and not any(
                c["leg"] == leg["symbol_or_underlying"] for c in conflicts):
            conflicts.append({
                "leg": leg["symbol_or_underlying"], "symbol": None,
                "requested": leg.get("qty"), "available": "0",
                "reason": "fully_allocated"})

    if conflicts:
        raise ValidationConflict(conflicts)
    return allocations


def _serialize_allocation(a: dict) -> dict:
    return {
        "symbol": a["symbol"],
        "sec_type": a["sec_type"],
        "qty": str(a["qty"]),
        "cost_basis_usd": None if a["cost_basis_usd"] is None else str(a["cost_basis_usd"]),
        "expiry": a["expiry"].isoformat() if a["expiry"] else None,
        "strike": None if a["strike"] is None else str(a["strike"]),
        "right": a["right"],
    }


def create_basket(engine: Engine, manifest: dict, dry_run: bool = False) -> dict:
    """Validate + match a manifest; persist basket, allocations, and audit row.

    dry_run=True computes and returns the would-be allocations WITHOUT
    writing anything (no basket, no allocations, no audit).
    """
    legs = _validate_manifest(manifest)
    slug = manifest["slug"].strip()

    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM baskets WHERE slug = :s"),
                              {"s": slug}).scalar()
        if exists:
            raise DuplicateSlug(f"basket slug already exists: {slug}")
        allocations = _compute_allocations(conn, legs)

        if not dry_run:
            basket_id = conn.execute(text(
                "INSERT INTO baskets (slug, name, thesis, source_ref, horizon, invalidation) "
                "VALUES (:slug, :name, :thesis, :src, :hz, :inv) RETURNING id"),
                {"slug": slug, "name": manifest["name"], "thesis": manifest["thesis"],
                 "src": manifest.get("source_ref"), "hz": manifest.get("horizon"),
                 "inv": manifest.get("invalidation")}).scalar_one()
            for a in allocations:
                conn.execute(text(
                    "INSERT INTO basket_allocations (basket_id, instrument_id, qty, cost_basis_usd) "
                    "VALUES (:b, :i, :q, :c)"),
                    {"b": basket_id, "i": a["instrument_id"],
                     "q": a["qty"], "c": a["cost_basis_usd"]})
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'basket.created', CAST(:p AS jsonb))"),
                {"p": json.dumps({"slug": slug, "legs": len(legs),
                                  "allocations": len(allocations)})})

    return {
        "slug": slug,
        "name": manifest["name"],
        "thesis": manifest["thesis"],
        "source_ref": manifest.get("source_ref"),
        "horizon": manifest.get("horizon"),
        "invalidation": manifest.get("invalidation"),
        "status": "open",
        "dry_run": dry_run,
        "allocations": [_serialize_allocation(a) for a in allocations],
    }


def basket_value(engine: Engine, basket_id: int) -> Decimal:
    """Current value: sum of allocation qty x last price x multiplier (cents)."""
    with engine.connect() as conn:
        value = conn.execute(text(
            "SELECT COALESCE(SUM(ba.qty * COALESCE(px.last_price_usd, 0) "
            "                 * COALESCE(i.multiplier, 1)), 0) "
            "FROM basket_allocations ba "
            "JOIN instruments i ON i.id = ba.instrument_id "
            "LEFT JOIN LATERAL (SELECT MAX(p.last_price_usd) AS last_price_usd "
            "                   FROM positions p WHERE p.instrument_id = i.id) px ON true "
            "WHERE ba.basket_id = :b"), {"b": basket_id}).scalar_one()
    return Decimal(value).quantize(_CENT)


def record_basket_snapshots(engine: Engine, taken_on) -> int:
    """Upsert one basket_snapshots row per open basket (idempotent per day)."""
    with engine.connect() as conn:
        basket_ids = [r.id for r in conn.execute(
            text("SELECT id FROM baskets WHERE status = 'open'")).all()]
    count = 0
    for basket_id in basket_ids:
        value = basket_value(engine, basket_id)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO basket_snapshots (basket_id, taken_on, value_usd) "
                "VALUES (:b, :d, :v) "
                "ON CONFLICT (basket_id, taken_on) DO UPDATE SET "
                "value_usd = EXCLUDED.value_usd"),
                {"b": basket_id, "d": taken_on, "v": value})
        count += 1
    return count
