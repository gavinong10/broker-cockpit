import threading

import httpx
from app.config import settings

def discord_message(title: str, description: str) -> dict:
    return {"embeds": [{"title": title, "description": description}]}

def _post(body: dict) -> None:
    try:
        httpx.post(settings.discord_webhook_url, json=body, timeout=10)
    except httpx.HTTPError:
        pass  # alerting must never crash the worker

def alert(title: str, description: str) -> None:
    if not settings.discord_webhook_url:
        return
    # Fire-and-forget thread: alert() is called from inside the asyncio event
    # loop (reconnect loop, ib_async event handlers) and must never block it.
    threading.Thread(target=_post, args=(discord_message(title, description),), daemon=True).start()
