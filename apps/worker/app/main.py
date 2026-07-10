from fastapi import FastAPI
from sqlalchemy import create_engine, text
from app.config import settings

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

@app.get("/health")
def health():
    return {"db": check_db(), "gateway": "not-configured"}
