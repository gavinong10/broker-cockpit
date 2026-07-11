"""Plan internal API: attach planned structures to a basket, list them.

Same conventions as app/baskets_api.py: internal-auth on every route,
Decimals serialized as strings. Domain logic lives in app/plans.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.internal_auth import require_internal
from app.plans import (DuplicatePlanLabel, PlanManifestError, UnknownBasket,
                       create_plan, list_plan_legs)

router = APIRouter(prefix="/internal/baskets", dependencies=[Depends(require_internal)])


def _get_engine():
    from app import main  # deferred: main imports this module at startup
    return main.get_engine()


class PlanImportRequest(BaseModel):
    manifest: dict


@router.post("/{slug}/plan")
def import_plan(slug: str, req: PlanImportRequest):
    try:
        return create_plan(_get_engine(), slug, req.manifest)
    except PlanManifestError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "plan_manifest", "message": str(exc)})
    except UnknownBasket:
        raise HTTPException(status_code=404, detail="unknown basket")
    except DuplicatePlanLabel as exc:
        raise HTTPException(status_code=409, detail={
            "error": "duplicate_plan_label", "labels": exc.labels})


@router.get("/{slug}/plan")
def get_plan(slug: str):
    try:
        return list_plan_legs(_get_engine(), slug)
    except UnknownBasket:
        raise HTTPException(status_code=404, detail="unknown basket")
