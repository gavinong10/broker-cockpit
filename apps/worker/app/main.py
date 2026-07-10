import asyncio
import json
import os

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from app.config import settings
from app.heartbeat import heartbeat_loop
from app.ibkr import gateway
from app.internal_auth import require_internal

app = FastAPI()
_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine

def check_db() -> str:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "down"

@app.on_event("startup")
def seed_owner():
    email = os.environ.get("OWNER_EMAIL")
    if not email:
        return
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO users (email, role, mask_amounts) VALUES (:e, 'owner', false) "
            "ON CONFLICT (email) DO NOTHING"), {"e": email})

def _audit_gateway_disconnect() -> None:
    try:
        with get_engine().begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', 'gateway.disconnect', '{}'::jsonb)"))
    except Exception:
        pass  # audit write must never crash the disconnect handler

gateway.ib.disconnectedEvent += _audit_gateway_disconnect

@app.on_event("startup")
async def start_gateway():
    asyncio.create_task(gateway.connect_forever())

def _heartbeat_status() -> dict:
    return {"db": check_db(), "gateway": "connected" if gateway.connected else "down"}

def _record_audit(category: str, payload: dict) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO audit_log (actor, category, payload) "
            "VALUES ('system', :c, CAST(:p AS jsonb))"),
            {"c": category, "p": json.dumps(payload)})

@app.on_event("startup")
async def start_heartbeat():
    asyncio.create_task(heartbeat_loop(_heartbeat_status, _record_audit))

@app.get("/health")
def health():
    return {"db": check_db(), "gateway": "connected" if gateway.connected else "down"}

@app.get("/internal/ping", dependencies=[Depends(require_internal)])
def internal_ping():
    return {"pong": True}
