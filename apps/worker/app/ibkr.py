import asyncio
from ib_async import IB
from app.config import settings
from app.notify import alert

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
        while True:
            if not self.connected:
                try:
                    await self.ib.connectAsync(settings.ib_gateway_host,
                                               settings.ib_gateway_port,
                                               clientId=settings.ib_client_id)
                    alert("gateway.connected", "IB Gateway session established")
                    delay = 5
                    if self.on_connect is not None:
                        self.on_connect()
                except Exception as e:
                    if delay > 60:  # suppress alerts during normal gateway boot
                        alert("gateway.connect_failed", f"{type(e).__name__}: {e}")
                    delay = min(delay * 2, 300)
            await asyncio.sleep(delay)

    def _on_disconnect(self) -> None:
        alert("gateway.disconnect", "IB Gateway disconnected — reconnect loop engaged")

gateway = Gateway()
