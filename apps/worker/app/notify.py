import httpx
from app.config import settings

def discord_message(title: str, description: str) -> dict:
    return {"embeds": [{"title": title, "description": description}]}

def alert(title: str, description: str) -> None:
    if not settings.discord_webhook_url:
        return
    try:
        httpx.post(settings.discord_webhook_url,
                   json=discord_message(title, description), timeout=10)
    except httpx.HTTPError:
        pass  # alerting must never crash the worker
