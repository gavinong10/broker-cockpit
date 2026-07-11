"""Trade journal API: the searchable 'why' behind positions.

Internal-auth like everything else; OWNER-ONLY enforcement lives in the web
layer (its server actions re-verify the role), because journal notes and
target/stop levels are free-form dollars that rendering-side masking cannot
reliably scrub. Full-text search runs on a stored tsvector (GIN-indexed).
"""
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.internal_auth import require_internal

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal)])

TAG_SUGGESTIONS = ["thesis", "add", "trim", "roll", "hedge", "iv-crush",
                   "earnings-play", "dca", "exit", "autopsy"]


class JournalCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    tag: str = Field(max_length=40)
    note: str
    actor: str = Field(min_length=1, max_length=320)
    target_usd: Decimal | None = None
    stop_usd: Decimal | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    source_ref: str | None = Field(default=None, max_length=128)


def _engine():
    from app.main import get_engine
    return get_engine()


def _row_dict(r) -> dict:
    return {
        "id": r.id,
        "symbol": r.symbol,
        "at": r.at.isoformat(),
        "actor": r.actor,
        "tag": r.tag,
        "note": r.note,
        "target_usd": str(r.target_usd) if r.target_usd is not None else None,
        "stop_usd": str(r.stop_usd) if r.stop_usd is not None else None,
        "confidence": r.confidence,
        "source_ref": r.source_ref,
    }


_COLS = "id, symbol, at, actor, tag, note, target_usd, stop_usd, confidence, source_ref"


@router.post("/journal")
def create_entry(body: JournalCreate):
    tag = body.tag.strip().lower()
    note = body.note.strip()
    if not tag:
        raise HTTPException(status_code=400, detail="tag is required")
    if not note:
        raise HTTPException(status_code=400, detail="note is required")
    with _engine().begin() as conn:
        row = conn.execute(text(
            "INSERT INTO journal_entries "
            "(symbol, actor, tag, note, target_usd, stop_usd, confidence, source_ref) "
            "VALUES (:symbol, :actor, :tag, :note, :target, :stop, :conf, :src) "
            f"RETURNING {_COLS}"),
            {"symbol": body.symbol.strip().upper(), "actor": body.actor, "tag": tag,
             "note": note, "target": body.target_usd, "stop": body.stop_usd,
             "conf": body.confidence, "src": body.source_ref}).one()
        conn.execute(text(
            "INSERT INTO audit_log (actor, category, payload) "
            "VALUES (:a, 'journal.created', CAST(:p AS jsonb))"),
            {"a": body.actor, "p": json.dumps({"id": row.id, "symbol": row.symbol, "tag": tag})})
    return _row_dict(row)


@router.get("/journal")
def list_entries(symbol: str | None = None,
                 tag: str | None = None,
                 q: str | None = None,
                 limit: int = Query(default=50, ge=1, le=200)):
    where, params = [], {"limit": limit}
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.strip().upper()
    if tag:
        where.append("tag = :tag")
        params["tag"] = tag.strip().lower()
    if q:
        where.append("tsv @@ plainto_tsquery('english', :q)")
        params["q"] = q
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT {_COLS} FROM journal_entries {clause} "
            "ORDER BY at DESC, id DESC LIMIT :limit"), params).all()
    return [_row_dict(r) for r in rows]


@router.delete("/journal/{entry_id}")
def delete_entry(entry_id: int):
    with _engine().begin() as conn:
        row = conn.execute(text(
            "DELETE FROM journal_entries WHERE id = :id RETURNING id, symbol, actor"),
            {"id": entry_id}).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="no such entry")
        conn.execute(text(
            "INSERT INTO audit_log (actor, category, payload) "
            "VALUES (:a, 'journal.deleted', CAST(:p AS jsonb))"),
            {"a": row.actor, "p": json.dumps({"id": row.id, "symbol": row.symbol})})
    return {"deleted": row.id}
