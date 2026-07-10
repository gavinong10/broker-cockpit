import os

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from app.config import settings
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

@app.get("/health")
def health():
    return {"db": check_db(), "gateway": "not-configured"}

@app.get("/internal/ping", dependencies=[Depends(require_internal)])
def internal_ping():
    return {"pong": True}
