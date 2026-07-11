"""Daily portfolio snapshots: one row per UTC day, taken after US close.

total_value_usd = SUM(qty * last_price * multiplier[default 1]) + SUM(cash)
per_account jsonb keyed "{broker}:{external_id}". Upsert on taken_on, so
re-runs (manual trigger, backfill) are idempotent per day.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.heartbeat import seconds_until_next

log = logging.getLogger(__name__)

SNAPSHOT_HOUR_UTC = 21
SNAPSHOT_MINUTE_UTC = 10  # 21:10 UTC, after the 21:00 heartbeat

_CENT = Decimal("0.01")


def compute_snapshot(engine: Engine) -> dict:
    """Pure read: current portfolio value per account and in total."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT a.broker, a.external_id, a.cash_usd, "
            "COALESCE(SUM(p.qty * p.last_price_usd * COALESCE(i.multiplier, 1)), 0) "
            "  AS positions_value_usd "
            "FROM broker_accounts a "
            "LEFT JOIN positions p ON p.broker_account_id = a.id "
            "LEFT JOIN instruments i ON i.id = p.instrument_id "
            "GROUP BY a.id, a.broker, a.external_id, a.cash_usd "
            "ORDER BY a.broker, a.external_id")).all()

    per_account: dict[str, dict] = {}
    total = Decimal("0")
    cash_total = Decimal("0")
    for row in rows:
        positions_value = Decimal(row.positions_value_usd).quantize(_CENT)
        cash = Decimal(row.cash_usd)
        value = positions_value + cash
        per_account[f"{row.broker}:{row.external_id}"] = {
            "positions_value_usd": str(positions_value),
            "cash_usd": str(cash),
            "value_usd": str(value),
        }
        total += value
        cash_total += cash

    return {
        "taken_on": datetime.now(timezone.utc).date(),
        "total_value_usd": total.quantize(_CENT),
        "cash_usd": cash_total.quantize(_CENT),
        "per_account": per_account,
    }


def record_snapshot(engine: Engine) -> dict:
    """Compute and upsert today's snapshot (idempotent on taken_on)."""
    snap = compute_snapshot(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
            "VALUES (:d, :total, :cash, CAST(:pa AS jsonb)) "
            "ON CONFLICT (taken_on) DO UPDATE SET "
            "total_value_usd = EXCLUDED.total_value_usd, "
            "cash_usd = EXCLUDED.cash_usd, "
            "per_account = EXCLUDED.per_account"),
            {"d": snap["taken_on"], "total": snap["total_value_usd"],
             "cash": snap["cash_usd"], "pa": json.dumps(snap["per_account"])})
        conn.execute(text(
            "INSERT INTO audit_log (actor, category, payload) "
            "VALUES ('system', 'snapshot.recorded', CAST(:p AS jsonb))"),
            {"p": json.dumps({
                "taken_on": snap["taken_on"].isoformat(),
                "total_value_usd": str(snap["total_value_usd"]),
                "accounts": len(snap["per_account"]),
            })})
    return snap


async def snapshot_loop(engine: Engine) -> None:
    """Daily snapshot at 21:10 UTC. Every iteration is exception-guarded —
    the loop must outlive transient DB failures."""
    while True:
        await asyncio.sleep(seconds_until_next(
            SNAPSHOT_HOUR_UTC, datetime.now(timezone.utc), minute=SNAPSHOT_MINUTE_UTC))
        try:
            snap = await asyncio.to_thread(record_snapshot, engine)
            log.info("snapshot recorded: %s total=%s",
                     snap["taken_on"], snap["total_value_usd"])
        except Exception as exc:
            log.warning("snapshot failed: %s: %s", exc.__class__.__name__, exc)
