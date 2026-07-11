import asyncio
import time
from datetime import datetime, timedelta, timezone

from app.notify import alert

FIRE_HOUR_UTC = 21  # 21:00 UTC daily

def seconds_until_next(hour: int, now: datetime, minute: int = 0) -> float:
    """Seconds until the next daily hh:mm (shared by heartbeat and snapshots)."""
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()

async def heartbeat_loop(get_status, record_audit) -> None:
    """Daily liveness ping: Discord embed + audit_log row.

    get_status: () -> dict (db/gateway state); record_audit: (category, payload) -> None.
    Each iteration is exception-guarded — the heartbeat must outlive any
    transient DB or webhook failure.
    """
    started = time.monotonic()
    while True:
        await asyncio.sleep(seconds_until_next(FIRE_HOUR_UTC, datetime.now(timezone.utc)))
        try:
            status = get_status()
            status["uptime_hours"] = round((time.monotonic() - started) / 3600, 1)
            alert("heartbeat", ", ".join(f"{k}: {v}" for k, v in status.items()))
            record_audit("system.heartbeat", status)
        except Exception:
            pass
