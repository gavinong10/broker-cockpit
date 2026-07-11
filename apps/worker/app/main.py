import asyncio
import json
import os

from dataclasses import asdict

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from app.config import settings
from app.heartbeat import heartbeat_loop
from app.ibkr import gateway
from app import ibkr_sync, rh_refresh
from app.internal_auth import require_internal
from app.portfolio_api import router as portfolio_router
from app.robinhood import RHAuthError, sync_robinhood
from app.scheduler import sync_loop
from app.snapshots import record_snapshot, snapshot_loop

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def _validation_error_no_echo(request: Request, exc: RequestValidationError):
    # FastAPI's default 422 echoes the submitted body in each error's "input"
    # field — which would reflect credentials (e.g. the RH refresh password)
    # back in the response. Strip everything but location and message.
    return JSONResponse(
        status_code=422,
        content={"detail": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]},
    )

app.include_router(portfolio_router)
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

def _gateway_state() -> str:
    if not settings.ib_enabled:
        return "disabled"
    return "connected" if gateway.connected else "down"

@app.on_event("startup")
async def start_gateway():
    if not settings.ib_enabled:
        return  # IB_ENABLED=false: no reconnect loop, no connect-failure alerts
    # on every successful connect, (re)start the 15-min IBKR position sync;
    # ibkr_sync.start_sync_task guards against double-starts on reconnects
    gateway.on_connect = lambda: ibkr_sync.start_sync_task(get_engine(), gateway)
    asyncio.create_task(gateway.connect_forever())

def _heartbeat_status() -> dict:
    return {"db": check_db(), "gateway": _gateway_state()}

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
    return {"db": check_db(), "gateway": _gateway_state()}

@app.on_event("startup")
async def start_sync_loop():
    asyncio.create_task(sync_loop(get_engine()))

@app.on_event("startup")
async def start_snapshot_loop():
    asyncio.create_task(snapshot_loop(get_engine()))

@app.get("/internal/ping", dependencies=[Depends(require_internal)])
def internal_ping():
    return {"pong": True}

@app.post("/internal/sync/robinhood", dependencies=[Depends(require_internal)])
async def trigger_robinhood_sync():
    try:
        result = await asyncio.to_thread(sync_robinhood, get_engine())
    except RHAuthError as exc:
        return JSONResponse(status_code=502, content={"error": "rh_auth", "detail": str(exc)})
    body = asdict(result)
    body["cash_usd"] = str(body["cash_usd"])  # Decimals serialize as strings
    return body

class RHRefreshRequest(BaseModel):
    # SECURITY: password is used for exactly one login() call inside
    # rh_refresh.refresh_session and is never persisted, logged, echoed in
    # errors, or written to audit payloads.
    username: str
    password: str
    code: str | None = None
    actor: str

# Device-approval ("prompt") challenges poll RH for up to ~2 minutes.
RH_REFRESH_TIMEOUT_S = 150

def _audit_rh_refresh(category: str, actor: str, payload: dict) -> None:
    # payload must only ever contain actor/channel/expires_in/detail — no secrets
    try:
        with get_engine().begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES (:a, :c, CAST(:p AS jsonb))"),
                {"a": actor, "c": category, "p": json.dumps({"actor": actor, **payload})})
    except Exception:
        pass  # audit failure must never mask the refresh outcome

@app.post("/internal/rh/refresh", dependencies=[Depends(require_internal)])
async def trigger_rh_refresh(req: RHRefreshRequest):
    _audit_rh_refresh("rh.refresh.requested", req.actor, {})
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(rh_refresh.refresh_session, req.username, req.password, req.code),
            timeout=RH_REFRESH_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _audit_rh_refresh("rh.refresh.failed", req.actor, {"detail": "timeout"})
        return JSONResponse(status_code=504, content={"status": "timeout"})
    except rh_refresh.NeedsCode as exc:
        _audit_rh_refresh("rh.refresh.needs_code", req.actor, {"channel": exc.channel})
        return JSONResponse(status_code=428,
                            content={"status": "needs_code", "channel": exc.channel})
    except rh_refresh.Busy:
        return JSONResponse(status_code=409, content={"status": "busy"})
    except rh_refresh.RHRefreshError as exc:
        # exc message is scrubbed of the password by rh_refresh
        _audit_rh_refresh("rh.refresh.failed", req.actor, {"detail": str(exc)})
        return JSONResponse(status_code=502,
                            content={"status": "failed", "detail": str(exc)})

    expires_in = result.get("expires_in")
    _audit_rh_refresh("rh.refresh.ok", req.actor, {"expires_in": expires_in})
    body: dict = {"status": "ok", "expires_in": expires_in}
    # Kick one sync so the fresh session is exercised immediately; a sync
    # failure degrades the response but must not fail the refresh itself.
    try:
        sync_result = await asyncio.to_thread(sync_robinhood, get_engine())
        sync_body = asdict(sync_result)
        sync_body["cash_usd"] = str(sync_body["cash_usd"])
        body["sync"] = sync_body
    except Exception as exc:
        body["sync"] = {"error": str(exc)}
    return body

@app.post("/internal/snapshots/run", dependencies=[Depends(require_internal)])
async def trigger_snapshot():
    snap = await asyncio.to_thread(record_snapshot, get_engine())
    return {
        "taken_on": snap["taken_on"].isoformat(),
        "total_value_usd": str(snap["total_value_usd"]),
        "cash_usd": str(snap["cash_usd"]),
        "per_account": snap["per_account"],
    }
