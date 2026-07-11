"""Basket plans: planned (not yet owned) structures, monitored against quotes.

A plan leg is one *structure* — e.g. a two-contract call vertical — with the
planned entry economics captured at design time. Plans never place orders;
they are monitored intent (docs/superpowers/plans/2026-07-11-basket-plan-monitor.md).
The monitor (app/plan_monitor.py, task 3) grades live entry cost vs. plan each
sync cycle; graduation to held happens when synced positions match (task 8).

Plan manifest schema (POST /internal/baskets/{slug}/plan):

{
  "legs": [
    {
      "label": "NBIS Dec-28 220/330",
      "structure": [
        {"occ": "NBIS281215C00220000", "sec_type": "OPT", "ratio": 1},
        {"occ": "NBIS281215C00330000", "sec_type": "OPT", "ratio": -1}
      ],
      "qty": "1",
      "planned_net_debit": "17.23",
      "tolerance_pct": "5",
      "breakeven_underlying": "237.23",
      "max_value_usd": "11000",
      "thesis_note": "short strike at street-high zone"
    }
  ]
}

- `structure`: 1-4 contract dicts. OPT entries need a full OCC symbol; STK
  entries need a plain symbol. `ratio` is a signed non-zero int (+1 long leg,
  -1 short leg); at least one ratio must be positive.
- `planned_net_debit` is per structure unit, per share (multiply by 100 for
  option-structure dollars). Net-credit structures are out of scope for now.
"""
import json
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.engine import Engine

_OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_STK_RE = re.compile(r"^[A-Z][A-Z.]{0,9}$")

_MAX_STRUCTURE_LEGS = 4


class PlanError(Exception):
    """Base for plan domain errors."""


class PlanManifestError(PlanError):
    """Malformed plan manifest. -> 400"""


class UnknownBasket(PlanError):
    """No basket with this slug. -> 404"""


class DuplicatePlanLabel(PlanError):
    """Plan legs with these labels already exist for the basket. -> 409"""

    def __init__(self, labels: list[str]):
        super().__init__(f"duplicate plan label(s): {', '.join(labels)}")
        self.labels = labels


def _dec(value, field: str, *, minimum: Decimal | None = None,
         maximum: Decimal | None = None) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PlanManifestError(f"{field} is not a number: {value!r}") from exc
    if minimum is not None and d < minimum:
        raise PlanManifestError(f"{field} must be >= {minimum}: {value!r}")
    if maximum is not None and d > maximum:
        raise PlanManifestError(f"{field} must be <= {maximum}: {value!r}")
    return d


def _validate_contract(entry, label: str) -> dict:
    if not isinstance(entry, dict):
        raise PlanManifestError(f"leg {label!r}: structure entries must be objects")
    ratio = entry.get("ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio == 0:
        raise PlanManifestError(f"leg {label!r}: ratio must be a non-zero integer")
    if abs(ratio) > _MAX_STRUCTURE_LEGS:
        raise PlanManifestError(f"leg {label!r}: |ratio| must be <= {_MAX_STRUCTURE_LEGS}")
    sec_type = entry.get("sec_type")
    if sec_type == "OPT":
        occ = entry.get("occ")
        if not isinstance(occ, str) or not _OCC_RE.match(occ):
            raise PlanManifestError(
                f"leg {label!r}: OPT entries need a full OCC symbol, got {occ!r}")
        return {"occ": occ, "sec_type": "OPT", "ratio": ratio}
    if sec_type == "STK":
        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not _STK_RE.match(symbol):
            raise PlanManifestError(
                f"leg {label!r}: STK entries need a plain symbol, got {symbol!r}")
        return {"symbol": symbol, "sec_type": "STK", "ratio": ratio}
    raise PlanManifestError(f"leg {label!r}: sec_type must be OPT or STK, got {sec_type!r}")


def structure_multiplier(structure: list[dict]) -> int:
    """Dollar multiplier for a structure unit: options => 100, stock-only => 1."""
    return 100 if any(c.get("sec_type") == "OPT" for c in structure) else 1


def _validate_leg(leg, index: int) -> dict:
    if not isinstance(leg, dict):
        raise PlanManifestError(f"legs[{index}] must be an object")
    label = leg.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 64:
        raise PlanManifestError(f"legs[{index}].label is required (<= 64 chars)")
    label = label.strip()
    structure = leg.get("structure")
    if not isinstance(structure, list) or not (1 <= len(structure) <= _MAX_STRUCTURE_LEGS):
        raise PlanManifestError(
            f"leg {label!r}: structure must be a list of 1-{_MAX_STRUCTURE_LEGS} contracts")
    contracts = [_validate_contract(c, label) for c in structure]
    if not any(c["ratio"] > 0 for c in contracts):
        raise PlanManifestError(f"leg {label!r}: at least one ratio must be positive")
    out = {
        "label": label,
        "structure": contracts,
        "qty": _dec(leg.get("qty"), f"leg {label!r}.qty", minimum=Decimal("0.00000001")),
        "planned_net_debit": _dec(leg.get("planned_net_debit"),
                                  f"leg {label!r}.planned_net_debit",
                                  minimum=Decimal("0.0001")),
        "tolerance_pct": _dec(leg.get("tolerance_pct", "5"),
                              f"leg {label!r}.tolerance_pct",
                              minimum=Decimal("0"), maximum=Decimal("100")),
        "breakeven_underlying": None,
        "max_value_usd": None,
        "thesis_note": None,
    }
    if leg.get("breakeven_underlying") is not None:
        out["breakeven_underlying"] = _dec(
            leg["breakeven_underlying"], f"leg {label!r}.breakeven_underlying",
            minimum=Decimal("0"))
    if leg.get("max_value_usd") is not None:
        out["max_value_usd"] = _dec(
            leg["max_value_usd"], f"leg {label!r}.max_value_usd", minimum=Decimal("0"))
    note = leg.get("thesis_note")
    if note is not None:
        if not isinstance(note, str):
            raise PlanManifestError(f"leg {label!r}: thesis_note must be a string")
        out["thesis_note"] = note.strip() or None
    return out


def validate_plan_manifest(manifest) -> list[dict]:
    if not isinstance(manifest, dict):
        raise PlanManifestError("plan manifest must be an object")
    legs = manifest.get("legs")
    if not isinstance(legs, list) or not legs:
        raise PlanManifestError("manifest.legs must be a non-empty list")
    validated = [_validate_leg(leg, i) for i, leg in enumerate(legs)]
    seen: set[str] = set()
    for leg in validated:
        if leg["label"] in seen:
            raise PlanManifestError(f"duplicate label in payload: {leg['label']!r}")
        seen.add(leg["label"])
    return validated


def planned_total_usd(legs: list[dict]) -> Decimal:
    total = Decimal("0")
    for leg in legs:
        total += leg["qty"] * leg["planned_net_debit"] * structure_multiplier(leg["structure"])
    return total.quantize(Decimal("0.01"))


def create_plan(engine: Engine, slug: str, manifest: dict) -> dict:
    """Validate and insert plan legs for a basket. One transaction; duplicate
    labels (vs. existing legs) reject the whole payload."""
    legs = validate_plan_manifest(manifest)
    with engine.begin() as conn:
        basket_id = conn.execute(
            text("SELECT id FROM baskets WHERE slug = :s"), {"s": slug}).scalar_one_or_none()
        if basket_id is None:
            raise UnknownBasket(f"unknown basket: {slug!r}")
        existing = {r.label for r in conn.execute(
            text("SELECT label FROM basket_plan_legs WHERE basket_id = :b"),
            {"b": basket_id}).all()}
        dupes = sorted(leg["label"] for leg in legs if leg["label"] in existing)
        if dupes:
            raise DuplicatePlanLabel(dupes)
        for leg in legs:
            conn.execute(text(
                "INSERT INTO basket_plan_legs (basket_id, label, structure, qty, "
                "planned_net_debit, tolerance_pct, breakeven_underlying, "
                "max_value_usd, thesis_note) "
                "VALUES (:b, :label, CAST(:structure AS jsonb), :qty, :debit, "
                ":tol, :be, :maxv, :note)"),
                {"b": basket_id, "label": leg["label"],
                 "structure": json.dumps(leg["structure"]),
                 "qty": leg["qty"], "debit": leg["planned_net_debit"],
                 "tol": leg["tolerance_pct"], "be": leg["breakeven_underlying"],
                 "maxv": leg["max_value_usd"], "note": leg["thesis_note"]})
        total = planned_total_usd(legs)
        conn.execute(text(
            "INSERT INTO audit_log (actor, category, payload) "
            "VALUES ('system', 'basket.plan_created', CAST(:p AS jsonb))"),
            {"p": json.dumps({"slug": slug, "legs": len(legs),
                              "planned_total_usd": str(total)})})
    return {"slug": slug, "created": len(legs),
            "planned_total_usd": str(total),
            "labels": [leg["label"] for leg in legs]}


def _s(value):
    return None if value is None else str(value)


_MARKS_PER_LEG = 40
_CURVE_MOVES = [Decimal(m) / 100 for m in range(-50, 125, 5)]   # -50% .. +120%


def list_plan_legs(engine: Engine, slug: str) -> dict:
    """Full plan view: legs + recent mark history + basket payoff curve.

    The payoff curve is expiry-intrinsic P&L vs a uniform underlying move,
    anchored on each leg's most recent marked spot. Legs whose payoff can't be
    modeled (no spot reference yet, or a structure with no closed form) are
    listed in curve_excluded rather than silently dropped.
    """
    with engine.connect() as conn:
        basket_id = conn.execute(
            text("SELECT id FROM baskets WHERE slug = :s"), {"s": slug}).scalar_one_or_none()
        if basket_id is None:
            raise UnknownBasket(f"unknown basket: {slug!r}")
        rows = conn.execute(text(
            "SELECT id, label, structure, qty, planned_net_debit, tolerance_pct, "
            "breakeven_underlying, max_value_usd, thesis_note, status, "
            "monitor_status, last_quote_net, last_quoted_at, filled_net_debit, "
            "created_at "
            "FROM basket_plan_legs WHERE basket_id = :b ORDER BY id"),
            {"b": basket_id}).all()
        marks_by_leg: dict[int, list] = {}
        if rows:
            mark_rows = conn.execute(text(
                "SELECT plan_leg_id, taken_at, net_cost, underlying_spot, quote_basis "
                "FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY m.plan_leg_id "
                "                                     ORDER BY m.taken_at DESC) AS rn "
                "      FROM basket_plan_marks m "
                "      WHERE m.plan_leg_id = ANY(:ids)) ranked "
                "WHERE rn <= :n ORDER BY plan_leg_id, taken_at"),
                {"ids": [r.id for r in rows], "n": _MARKS_PER_LEG}).all()
            for m in mark_rows:
                marks_by_leg.setdefault(m.plan_leg_id, []).append(m)

    legs, curve_inputs, curve_excluded = [], [], []
    planned_total = Decimal("0")
    for r in rows:
        structure = r.structure if isinstance(r.structure, list) else json.loads(r.structure)
        leg_marks = marks_by_leg.get(r.id, [])
        latest_spot = next((Decimal(m.underlying_spot) for m in reversed(leg_marks)
                            if m.underlying_spot is not None), None)
        planned = Decimal(r.planned_net_debit)
        mult = structure_multiplier(structure)
        planned_total += Decimal(r.qty) * planned * mult
        delta_pct = None
        if r.last_quote_net is not None and planned:
            delta_pct = str(((Decimal(r.last_quote_net) / planned - 1) * 100
                             ).quantize(Decimal("0.1")))
        legs.append({
            "label": r.label,
            "structure": structure,
            "qty": str(r.qty),
            "planned_net_debit": str(planned),
            "tolerance_pct": str(r.tolerance_pct),
            "breakeven_underlying": _s(r.breakeven_underlying),
            "max_value_usd": _s(r.max_value_usd),
            "thesis_note": r.thesis_note,
            "status": r.status,
            "monitor_status": r.monitor_status,
            "last_quote_net": _s(r.last_quote_net),
            "last_quote_delta_pct": delta_pct,
            "last_quoted_at": r.last_quoted_at.isoformat() if r.last_quoted_at else None,
            "filled_net_debit": _s(r.filled_net_debit),
            "created_at": r.created_at.isoformat(),
            "marks": [{"taken_at": m.taken_at.isoformat(),
                       "net_cost": _s(m.net_cost),
                       "underlying_spot": _s(m.underlying_spot),
                       "quote_basis": m.quote_basis} for m in leg_marks],
        })
        payoff = _leg_payoff_fn(structure, latest_spot)
        if payoff is None:
            curve_excluded.append(r.label)
        else:
            curve_inputs.append((Decimal(r.qty), mult, planned, payoff))

    curve = []
    if curve_inputs:
        for move in _CURVE_MOVES:
            pnl = Decimal("0")
            for qty, mult, planned, payoff in curve_inputs:
                pnl += qty * mult * (payoff(move) - planned)
            curve.append({"move_pct": str((move * 100).quantize(Decimal("1"))),
                          "pnl_usd": str(pnl.quantize(Decimal("0.01")))})

    return {"slug": slug, "legs": legs,
            "planned_total_usd": str(planned_total.quantize(Decimal("0.01"))),
            "payoff_curve": curve, "curve_excluded": curve_excluded}


def _leg_payoff_fn(structure: list[dict], spot: Decimal | None):
    """Expiry-intrinsic value per share as f(uniform move) for modelable
    structures: all-OPT call structures (verticals, singles) and pure stock.
    Returns None when no spot anchor exists or the shape has no closed form."""
    if spot is None or spot <= 0:
        return None
    if all(c.get("sec_type") == "STK" for c in structure):
        net_ratio = sum(c["ratio"] for c in structure)
        return lambda m: spot * (1 + m) * net_ratio
    if not all(c.get("sec_type") == "OPT" for c in structure):
        return None
    try:
        parsed = []
        for c in structure:
            from app.plan_monitor import parse_occ  # local import: avoid cycle at module load
            _, _, right, strike = parse_occ(c["occ"])
            if right != "C":
                return None                          # puts: not modeled yet
            parsed.append((strike, Decimal(c["ratio"])))
    except (ValueError, KeyError):
        return None

    def value(move: Decimal) -> Decimal:
        s_t = spot * (1 + move)
        total = Decimal("0")
        for strike, ratio in parsed:
            total += ratio * max(s_t - strike, Decimal("0"))
        return total

    return value
