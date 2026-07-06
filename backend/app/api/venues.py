import asyncio
from typing import Any, Dict

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.venue import BudgetPlannerRequest, BudgetPlannerResponse, CateringGuideResponse, VenueSearchRequest, VenueSearchResponse
from app.services import canvas_service
from app.services.smart_service import smart_budget_planner, smart_catering_guide, smart_search_venues

router = APIRouter(prefix="/venues", tags=["venues"])


@router.post("/search", response_model=VenueSearchResponse)
async def search_venues(
    body: VenueSearchRequest,
    _: User = Depends(get_current_user),
):
    try:
        return await smart_search_venues(body)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/budget", response_model=BudgetPlannerResponse)
async def budget_planner(
    body: BudgetPlannerRequest,
    _: User = Depends(get_current_user),
):
    try:
        return await smart_budget_planner(body.event_type, body.total_budget, body.currency)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/canvas-test")
async def canvas_test(_: User = Depends(get_current_user)):
    """
    Connectivity check for Canvas Events.
    Returns status of the /data-feed endpoint and a sample listing page.
    """
    base = "https://www.canvas-events.co.uk"
    results = {}
    for label, url, is_json in [
        ("api_venues",    f"{base}/api/venues", True),
        ("listing_london", f"{base}/event/hire/conference/venues/london", False),
        ("homepage", base, False),
    ]:
        try:
            hdrs = dict(canvas_service._JSON_HEADERS if is_json else canvas_service._HEADERS)
            r = requests.get(url, headers=hdrs, timeout=10)
            content_type = r.headers.get("content-type", "")
            results[label] = {
                "status": r.status_code,
                "content_type": content_type,
                "size_bytes": len(r.content),
                "ok": r.status_code == 200,
            }
            if is_json and r.status_code == 200:
                try:
                    payload = r.json()
                    results[label]["json_keys"] = list(payload.keys())[:10]
                    results[label]["record_count"] = len(payload.get("data") or [])
                except Exception as e:
                    results[label]["json_error"] = str(e)
        except Exception as exc:
            results[label] = {"error": str(exc), "ok": False}
    return results


@router.post("/enrich")
async def enrich_venue(
    venue: Dict[str, Any] = Body(...),
    _: User = Depends(get_current_user),
):
    """Fetch Canvas Events venue detail page and fill in any empty fields."""
    try:
        return await asyncio.to_thread(canvas_service.enrich_canvas_venue, venue)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/catering-guide", response_model=CateringGuideResponse)
async def catering_guide(
    event_type: str = Query("corporate"),
    _: User = Depends(get_current_user),
):
    try:
        return await smart_catering_guide(event_type)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
