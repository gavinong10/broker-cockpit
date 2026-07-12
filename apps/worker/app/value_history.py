"""Value history: RH equity backfill + bank-transfer ingestion.

backfill_snapshots: seed pre-go-live daily snapshots from Robinhood's
portfolio-equity records (source='backfill_rh'); NEVER touches days that
already have a row — observed history always wins.

sync_cash_flows: upsert completed RH ACH transfers into cash_flows,
idempotent via source_ref 'rh-ach:{id}' (the schema's unique column was
designed for exactly this). ACATS/wires aren't exposed by robin_stocks;
enter those manually if they ever happen.

Both fetchers are injectable for tests (pattern: plan_monitor.quote_fn).
Read-only with respect to trading.
"""
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.robinhood import rh_session

log = logging.getLogger(__name__)

_CENT = Decimal("0.01")

_FLOW_KIND = {"deposit": ("deposit", 1), "withdraw": ("withdrawal", -1)}


def _fetch_equity_historicals(span: str) -> list[dict]:
    import robin_stocks.robinhood as rh
    rh_session()
    data = rh.get_historical_portfolio(interval="day", span=span) or {}
    return data.get("equity_historicals") or []


def _fetch_bank_transfers() -> list[dict]:
    import robin_stocks.robinhood as rh
    rh_session()
    return rh.get_bank_transfers() or []


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def backfill_snapshots(engine: Engine, span: str = "year", fetch_fn=None) -> dict:
    """Insert daily snapshots for days with no existing row, from RH equity
    history. Existing rows (any source) are never modified."""
    items = (fetch_fn or _fetch_equity_historicals)(span)
    with engine.connect() as conn:
        rh_acct = conn.execute(text(
            "SELECT external_id FROM broker_accounts WHERE broker = 'robinhood' "
            "ORDER BY id LIMIT 1")).scalar_one_or_none()
        existing = {r.taken_on for r in conn.execute(
            text("SELECT taken_on FROM snapshots")).all()}
    key = f"robinhood:{rh_acct or 'unknown'}"

    inserted, skipped_existing, skipped_bad = 0, 0, 0
    with engine.begin() as conn:
        for item in items:
            begins = item.get("begins_at") or ""
            value = (_dec(item.get("adjusted_close_equity"))
                     or _dec(item.get("close_equity"))
                     or _dec(item.get("open_equity")))
            try:
                day = datetime.fromisoformat(begins.replace("Z", "+00:00")).date()
            except ValueError:
                day = None
            if day is None or value is None:
                skipped_bad += 1
                continue
            if day in existing:
                skipped_existing += 1
                continue
            conn.execute(text(
                "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, "
                "per_account, source) "
                "VALUES (:d, :v, 0, CAST(:pa AS jsonb), 'backfill_rh') "
                "ON CONFLICT (taken_on) DO NOTHING"),
                {"d": day, "v": value.quantize(_CENT),
                 "pa": json.dumps({key: {"value_usd": str(value.quantize(_CENT)),
                                         "backfilled": True}})})
            existing.add(day)
            inserted += 1
        if inserted:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'snapshot.backfill', CAST(:p AS jsonb))"),
                {"p": json.dumps({"span": span, "inserted": inserted,
                                  "skipped_existing": skipped_existing,
                                  "skipped_bad": skipped_bad})})
    return {"inserted": inserted, "skipped_existing": skipped_existing,
            "skipped_bad": skipped_bad}


def sync_cash_flows(engine: Engine, fetch_fn=None) -> dict:
    """Upsert completed RH bank transfers into cash_flows (idempotent)."""
    transfers = (fetch_fn or _fetch_bank_transfers)()
    with engine.connect() as conn:
        acct_id = conn.execute(text(
            "SELECT id FROM broker_accounts WHERE broker = 'robinhood' "
            "ORDER BY id LIMIT 1")).scalar_one_or_none()
    if acct_id is None:
        return {"inserted": 0, "considered": 0, "skipped": len(transfers),
                "note": "no robinhood account row"}

    inserted, considered, skipped = 0, 0, 0
    with engine.begin() as conn:
        for t in transfers:
            if t.get("state") != "completed":
                skipped += 1
                continue
            kind = _FLOW_KIND.get(t.get("direction"))
            amount = _dec(t.get("amount"))
            ref = t.get("id")
            occurred = t.get("updated_at") or t.get("created_at") or ""
            try:
                occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = None
            if kind is None or amount is None or not ref or occurred_at is None:
                skipped += 1
                continue
            considered += 1
            flow_kind, sign = kind
            res = conn.execute(text(
                "INSERT INTO cash_flows (broker_account_id, occurred_at, kind, "
                "amount_usd, currency, source_ref) "
                "VALUES (:a, :at, :k, :amt, 'USD', :ref) "
                "ON CONFLICT (source_ref) DO NOTHING"),
                {"a": acct_id, "at": occurred_at, "k": flow_kind,
                 "amt": (sign * amount).quantize(_CENT), "ref": f"rh-ach:{ref}"})
            inserted += res.rowcount
        if inserted:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'cashflows.synced', CAST(:p AS jsonb))"),
                {"p": json.dumps({"inserted": inserted, "considered": considered})})
    return {"inserted": inserted, "considered": considered, "skipped": skipped}
