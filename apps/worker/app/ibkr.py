import asyncio
import time

from ib_async import IB
from app.config import settings
from app.notify import alert

_CONNECT_FAILED_ALERT_INTERVAL_S = 6 * 3600  # repeated failures alert at most every 6h

class Gateway:
    def __init__(self) -> None:
        self.ib = IB()
        self.ib.disconnectedEvent += self._on_disconnect
        self.on_connect = None  # optional hook, set by main.py; fired after each successful connect

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    async def connect_forever(self) -> None:
        delay = 5
        last_fail_alert = 0.0
        while True:
            if not self.connected:
                try:
                    await self.ib.connectAsync(settings.ib_gateway_host,
                                               settings.ib_gateway_port,
                                               clientId=settings.ib_client_id)
                    alert("gateway.connected", "IB Gateway session established")
                    delay = 5
                    last_fail_alert = 0.0
                    if self.on_connect is not None:
                        self.on_connect()
                except Exception as e:
                    # Suppress during normal gateway boot (delay <= 60), then
                    # throttle: an unreachable gateway retries every 300s and
                    # would otherwise spam Discord every 5 minutes.
                    now = time.monotonic()
                    if delay > 60 and now - last_fail_alert > _CONNECT_FAILED_ALERT_INTERVAL_S:
                        alert("gateway.connect_failed",
                              f"{type(e).__name__}: {e} (further failures muted for 6h)")
                        last_fail_alert = now
                    delay = min(delay * 2, 300)
            await asyncio.sleep(delay)

    def _on_disconnect(self) -> None:
        alert("gateway.disconnect", "IB Gateway disconnected — reconnect loop engaged")

gateway = Gateway()
