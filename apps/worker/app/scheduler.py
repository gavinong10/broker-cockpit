"""Robinhood sync scheduler: market-hours cadence, staleness, failure alerts.

Staleness thresholds live here (writer side) and are imported by the
portfolio API (reader side) so the two can never drift apart.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, time as dtime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.notify import alert
from app.plan_monitor import monitor_plans
from app.robinhood import RHAuthError, sync_robinhood

log = logging.getLogger(__name__)

MARKET_OPEN_UTC = dtime(13, 30)
MARKET_CLOSE_UTC = dtime(20, 0)

SYNC_DELAY_MARKET_SECONDS = 900    # 15 min during US market hours
SYNC_DELAY_OFF_SECONDS = 3600      # hourly off-hours

STALE_MARKET_SECONDS = 45 * 60     # last_synced_at older than this is stale in-market
STALE_OFF_SECONDS = 2 * 3600       # ... or this off-hours

AUTH_ALERT_COOLDOWN_SECONDS = 6 * 3600
CONSECUTIVE_FAILURE_ALERT_THRESHOLD = 3

# Module state: last auth-expired Discord alert (time.monotonic()), None = never.
_last_auth_alert_monotonic: float | None = None


def is_market_hours(dt: datetime) -> bool:
    """True during US market hours: 13:30 <= UTC time < 20:00, Mon-Fri."""
    dt = dt.astimezone(timezone.utc)
    return dt.weekday() < 5 and MARKET_OPEN_UTC <= dt.time() < MARKET_CLOSE_UTC


def next_sync_delay(dt: datetime) -> int:
    return SYNC_DELAY_MARKET_SECONDS if is_market_hours(dt) else SYNC_DELAY_OFF_SECONDS


def is_stale(last_synced_at: datetime | None, now: datetime) -> bool:
    """A never-synced or too-old account is stale; threshold depends on market hours."""
    if last_synced_at is None:
        return True
    threshold = STALE_MARKET_SECONDS if is_market_hours(now) else STALE_OFF_SECONDS
    return (now - last_synced_at).total_seconds() > threshold


def _maybe_alert_auth_expired(engine: Engine, exc: RHAuthError) -> None:
    global _last_auth_alert_monotonic
    now = time.monotonic()
    if (_last_auth_alert_monotonic is None
            or now - _last_auth_alert_monotonic >= AUTH_ALERT_COOLDOWN_SECONDS):
        _last_auth_alert_monotonic = now
        alert("Robinhood session expired",
              "Re-run scripts/rh_login.py and scp the pickle")
    _audit_auth_expired(engine, exc)


def _audit_auth_expired(engine: Engine, exc: RHAuthError) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'sync.robinhood.auth_expired', CAST(:p AS jsonb))"),
                {"p": json.dumps({"detail": str(exc)})})
    except Exception:
        pass  # audit write must never crash the sync loop


async def sync_loop(engine: Engine) -> None:
    """Periodic RH mirror sync. Every iteration is exception-guarded — the loop
    must outlive auth expiry, network flaps, and DB hiccups."""
    consecutive_failures = 0
    while True:
        await asyncio.sleep(next_sync_delay(datetime.now(timezone.utc)))
        try:
            result = await asyncio.to_thread(sync_robinhood, engine)
            consecutive_failures = 0
            log.info("robinhood sync ok: account=%s equities=%d options=%d",
                     result.account_external_id, result.equity_positions,
                     result.option_positions)
            # Plan monitor rides the same cadence; the RH session was just
            # validated by the sync. Its failures must never sink the loop.
            try:
                summary = await asyncio.to_thread(monitor_plans, engine)
                if summary["checked"]:
                    log.info("plan monitor: %s", summary)
            except Exception as exc:
                log.warning("plan monitor failed: %s: %s",
                            exc.__class__.__name__, exc)
        except RHAuthError as exc:
            log.warning("robinhood sync auth expired: %s", exc)
            _maybe_alert_auth_expired(engine, exc)
        except Exception as exc:
            consecutive_failures += 1
            log.warning("robinhood sync failed (%d consecutive): %s: %s",
                        consecutive_failures, exc.__class__.__name__, exc)
            if consecutive_failures == CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
                alert("Robinhood sync failing",
                      f"{consecutive_failures} consecutive failures; latest: "
                      f"{exc.__class__.__name__}: {exc}")
