"""Feature-factory API (internal-auth). State machine:
created -> building -> built | failed* | killed -> accepted -> reverted
                                 \\-> discarded
Accept/revert are the ONLY operations that touch the main branch, ever.
Kill switch: /runner/pause + /runner/resume (host flag; create/build refuse
while paused); /{slug}/kill terminates a running build.
"""
import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from app import features
from app.internal_auth import require_internal

router = APIRouter(prefix="/internal/features", dependencies=[Depends(require_internal)])


def _engine():
    from app.main import get_engine
    return get_engine()


class CreateReq(BaseModel):
    prompt: str
    model: str | None = None  # "fable" | "opus" | "sonnet"
    actor: str


class ActorReq(BaseModel):
    actor: str


_LIST_SQL = text(
    "SELECT slug, prompt, model, status, diff_stat, risky_paths, merge_sha, "
    "created_at, updated_at FROM features ORDER BY id DESC")


@router.get("")
def list_features():
    engine = _engine()
    with engine.connect() as conn:
        rows = conn.execute(_LIST_SQL).mappings().all()
    # Reconcile non-terminal rows with the host: a build whose starting request
    # died keeps running host-side; adopt its real outcome on every poll.
    if features.reconcile_features(engine, [dict(r) for r in rows]):
        with engine.connect() as conn:
            rows = conn.execute(_LIST_SQL).mappings().all()
    return [{**dict(r),
             "created_at": r["created_at"].isoformat(),
             "updated_at": r["updated_at"].isoformat()} for r in rows]


@router.get("/runner")
def runner_state():
    return features.runner_status()


@router.post("/runner/pause")
async def pause(req: ActorReq):
    try:
        return await asyncio.to_thread(features.set_paused, _engine(), True, req.actor)
    except features.RunnerError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.post("/runner/resume")
async def resume(req: ActorReq):
    try:
        return await asyncio.to_thread(features.set_paused, _engine(), False, req.actor)
    except features.RunnerError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.post("")
async def create(req: CreateReq):
    prompt = req.prompt.strip()
    if len(prompt) < 20:
        return JSONResponse(status_code=400, content={"error": "prompt too short"})
    busy = features.building_slug()
    if busy:
        return JSONResponse(status_code=409, content={
            "error": f"a build for '{busy}' is already in progress — wait for it to finish"})
    try:
        created = await asyncio.to_thread(
            features.create_feature, _engine(), prompt, req.model, req.actor)
    except features.RunnerError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    # Async start: spawn the multi-minute build in a daemon thread and return
    # 202 immediately. The UI polls the list endpoint, which reconciles the
    # row from the host — so even if this process dies, the outcome is adopted.
    try:
        features.start_build(_engine(), created["slug"], created["model"], req.actor)
    except features.BuildInProgress as exc:  # lost the race to another request
        return JSONResponse(status_code=409, content={"error": str(exc)})
    return JSONResponse(status_code=202, content={**created, "status": "building"})


@router.get("/{slug}")
def detail(slug: str):
    with _engine().connect() as conn:
        row = conn.execute(text(
            "SELECT slug, prompt, model, status, diff_stat, risky_paths, merge_sha, report, "
            "created_at, updated_at FROM features WHERE slug = :s"), {"s": slug}).mappings().first()
    if not row:
        return JSONResponse(status_code=404, content={"detail": "unknown feature"})
    d = dict(row)
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    return d


@router.get("/{slug}/diff", response_class=PlainTextResponse)
def diff(slug: str):
    try:
        return features.feature_diff(slug)
    except features.RunnerError as exc:
        return PlainTextResponse(str(exc), status_code=502)


@router.post("/{slug}/sync")
async def sync(slug: str):
    try:
        return await asyncio.to_thread(features.sync_feature, _engine(), slug)
    except features.RunnerError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.post("/{slug}/kill")
async def kill(slug: str, req: ActorReq):
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT status FROM features WHERE slug=:s"),
                           {"s": slug}).mappings().first()
    if not row:
        return JSONResponse(status_code=404, content={"detail": "unknown feature"})
    if row["status"] not in ("created", "building"):
        return JSONResponse(status_code=409, content={"error": f"no running build for status '{row['status']}'"})
    try:
        return await asyncio.to_thread(features.kill_feature, _engine(), slug, req.actor)
    except features.RunnerError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@router.post("/{slug}/accept")
async def accept(slug: str, req: ActorReq):
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT status, risky_paths FROM features WHERE slug=:s"),
                           {"s": slug}).mappings().first()
    if not row:
        return JSONResponse(status_code=404, content={"detail": "unknown feature"})
    if row["status"] != "built":
        return JSONResponse(status_code=409, content={"error": f"not acceptable from status '{row['status']}'"})
    try:
        merge_sha = await asyncio.to_thread(features.accept_feature, _engine(), slug, req.actor)
    except features.RunnerError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return {"status": "accepted", "merge_sha": merge_sha}


@router.post("/{slug}/revert")
async def revert(slug: str, req: ActorReq):
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT status, merge_sha FROM features WHERE slug=:s"),
                           {"s": slug}).mappings().first()
    if not row:
        return JSONResponse(status_code=404, content={"detail": "unknown feature"})
    if row["status"] == "accepted" and row["merge_sha"]:
        try:
            await asyncio.to_thread(features.revert_feature, _engine(), slug, row["merge_sha"], req.actor)
        except features.RunnerError as exc:
            return JSONResponse(status_code=502, content={"error": str(exc)})
        return {"status": "reverted"}
    if row["status"] in ("built", "failed", "failed_no_changes", "failed_blocked_paths", "killed", "created", "unknown"):
        try:
            await asyncio.to_thread(features.discard_feature, _engine(), slug, req.actor)
        except features.RunnerError as exc:
            return JSONResponse(status_code=502, content={"error": str(exc)})
        return {"status": "discarded"}
    return JSONResponse(status_code=409, content={"error": f"cannot revert from status '{row['status']}'"})
