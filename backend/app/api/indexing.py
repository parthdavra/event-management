import asyncio
import threading
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.database import SessionLocal
from app.models.user import User
from app.services import canvas_service, indexing_service

router = APIRouter(prefix="/indexing", tags=["indexing"])

# ── In-memory job store for long-running bulk operations ──────────────────────
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _job_set(jid: str, payload: Dict[str, Any]) -> None:
    with _jobs_lock:
        _jobs[jid] = payload


def _job_get(jid: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        return _jobs.get(jid)


class SourceOut(BaseModel):
    id: int
    source_name: str
    source_type: Optional[str] = None
    chunk_count: int
    status: str
    collection_name: Optional[str] = None
    indexed_at: Optional[str] = None


class IndexCityRequest(BaseModel):
    city: str
    venues: List[dict]
    replace_existing: bool = True


class IndexEventPlanRequest(BaseModel):
    event_name: str
    collection_slug: str
    document_text: str
    venues: List[dict]
    city: str


class IndexEventTypeRequest(BaseModel):
    event_type: str
    city: str
    venues: List[dict]
    replace_existing: bool = True


class IndexFromJsonRequest(BaseModel):
    venues: List[Dict[str, Any]]
    event_type: str = "general"
    city: str = ""
    replace_existing: bool = True


class BulkIndexRequest(BaseModel):
    cities: List[str]
    event_types: List[str]
    categories: List[str]
    radius_km: int = 5
    max_venues_per_city: int = 300
    replace_existing: bool = True


class FeedrIndexRequest(BaseModel):
    city: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    replace_existing: bool = True


class CateringGroup(BaseModel):
    label: str
    count: int
    dietary_type: str


class CateringMatchRequest(BaseModel):
    lat: float
    lon: float
    groups: List[CateringGroup]
    budget: Optional[float] = None


@router.post("/extract")
async def extract_text(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    """Extract raw text from a PDF, DOCX, or TXT file without indexing."""
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF, DOCX, and TXT files are supported",
        )
    file_bytes = await file.read()
    try:
        if ext == "pdf":
            text = indexing_service.extract_text_from_pdf(file_bytes)
        elif ext == "docx":
            text = indexing_service.extract_text_from_docx(file_bytes)
        else:
            text = file_bytes.decode("utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return {"text": text, "filename": file.filename}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    source_name: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index a PDF, DOCX, or TXT file into ChromaDB."""
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF, DOCX, and TXT files are supported",
        )

    file_bytes = await file.read()
    name = (source_name or "").strip() or file.filename

    try:
        if ext == "pdf":
            text = indexing_service.extract_text_from_pdf(file_bytes)
            ftype = "pdf"
        elif ext == "docx":
            text = indexing_service.extract_text_from_docx(file_bytes)
            ftype = "docx"
        else:
            text = file_bytes.decode("utf-8", errors="ignore")
            ftype = "txt"
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    source_id, error = indexing_service.index_source(db, current_user.id, name, ftype, text)
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    return {"source_id": source_id, "source_name": name}


@router.post("/text", status_code=status.HTTP_201_CREATED)
def index_text(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index raw text into ChromaDB."""
    source_name = body.get("source_name", "").strip()
    text = body.get("text", "").strip()
    if not source_name or not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_name and text are required",
        )
    source_id, error = indexing_service.index_source(db, current_user.id, source_name, "raw_text", text)
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)
    return {"source_id": source_id, "source_name": source_name}


@router.post("/city", status_code=status.HTTP_201_CREATED)
def index_city(
    body: IndexCityRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index venue data fetched from city APIs."""
    source_id, error = indexing_service.index_city_data(
        db, current_user.id, body.city, body.venues, body.replace_existing
    )
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)
    return {"source_id": source_id}


@router.post("/event-plan", status_code=status.HTTP_201_CREATED)
def index_event_plan(
    body: IndexEventPlanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index an event brief + fetched venues into a single named collection."""
    source_id, error = indexing_service.index_event_plan(
        db,
        current_user.id,
        body.event_name,
        body.collection_slug,
        body.document_text,
        body.venues,
        body.city,
    )
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)
    return {"source_id": source_id}


@router.post("/event-type", status_code=status.HTTP_201_CREATED)
def index_event_type(
    body: IndexEventTypeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Index venues into a per-event-type collection with rich + raw-JSON chunks."""
    source_id, error = indexing_service.index_event_type_venues(
        db, current_user.id, body.event_type, body.city, body.venues, body.replace_existing
    )
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)
    col = indexing_service.get_event_type_collection_name(current_user.id, body.event_type)
    return {"source_id": source_id, "collection_name": col}


@router.post("/bulk-event-types")
def bulk_index_event_types_start(
    body: BulkIndexRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Start bulk indexing in a background thread and return a job_id immediately.
    Poll GET /indexing/bulk-event-types/{job_id} for status and results.
    """
    jid = str(uuid.uuid4())
    _job_set(jid, {"status": "running", "result": None, "error": None})

    user_id = current_user.id
    cities = body.cities
    event_types = body.event_types
    categories = body.categories
    radius_km = body.radius_km
    max_venues_per_city = body.max_venues_per_city
    replace_existing = body.replace_existing

    def _run():
        db = SessionLocal()
        try:
            stats = indexing_service.bulk_index_event_types(
                db, user_id, cities, event_types, categories,
                radius_km, max_venues_per_city, replace_existing,
            )
            _job_set(jid, {"status": "done", "result": stats, "error": None})
        except Exception as exc:
            _job_set(jid, {"status": "error", "result": None, "error": str(exc)})
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": jid, "status": "running"}


@router.get("/bulk-event-types/{job_id}")
def bulk_index_job_status(
    job_id: str,
    _: User = Depends(get_current_user),
):
    """Poll for bulk-index job status / results."""
    job = _job_get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/from-json", status_code=status.HTTP_201_CREATED)
async def index_from_json(
    body: IndexFromJsonRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Accept raw venue JSON, enrich Canvas venues from their detail page,
    then index everything as rich-text + raw-JSON chunks.

    Returns per-venue enrichment results so the caller knows what was filled.
    """
    enriched_venues: List[Dict[str, Any]] = []
    enrichment_results: List[Dict[str, Any]] = []

    for v in body.venues:
        website = v.get("website") or ""
        is_canvas = "canvas-events.co.uk" in website
        if is_canvas:
            try:
                enriched = await asyncio.to_thread(canvas_service.enrich_canvas_venue, v)
                enriched_venues.append(enriched)
                enrichment_results.append({
                    "name": v.get("name", ""),
                    "enriched": enriched.get("_enriched", False),
                    "fields_filled": enriched.get("_enrich_fields_filled", []),
                    "has_price_guide": bool(enriched.get("canvas_price_guide")),
                    "has_capacity": bool(enriched.get("canvas_capacity_detail")),
                    "has_spaces": bool(enriched.get("canvas_spaces")),
                    "has_features": bool(enriched.get("canvas_features")),
                    "has_perfect_for": bool(enriched.get("canvas_perfect_for")),
                })
            except Exception as exc:
                enriched_venues.append(v)
                enrichment_results.append({
                    "name": v.get("name", ""),
                    "enriched": False,
                    "error": str(exc),
                    "fields_filled": [],
                })
        else:
            enriched_venues.append(v)
            enrichment_results.append({
                "name": v.get("name", ""),
                "enriched": False,
                "fields_filled": [],
                "note": "not a Canvas Events venue",
            })

    source_id, error = indexing_service.index_event_type_venues(
        db, current_user.id, body.event_type, body.city, enriched_venues, body.replace_existing
    )
    if error:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error)

    col = indexing_service.get_event_type_collection_name(current_user.id, body.event_type)
    return {
        "source_id": source_id,
        "collection_name": col,
        "total_venues": len(enriched_venues),
        "canvas_enriched": sum(1 for r in enrichment_results if r.get("enriched")),
        "enrichment_results": enrichment_results,
    }


@router.post("/feedr")
def feedr_index_start(
    body: FeedrIndexRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Scrape feedr.co catering vendors for a city and index them into ChromaDB.
    Runs in a background thread — poll GET /indexing/feedr/{job_id} for status.
    """
    from app.services import feedr_service

    jid = str(uuid.uuid4())
    _job_set(jid, {"status": "running", "result": None, "error": None})

    user_id = current_user.id
    city = body.city.strip()
    lat = body.lat
    lon = body.lon
    replace_existing = body.replace_existing

    def _run():
        db = SessionLocal()
        try:
            # Geocode if lat/lon not provided
            nonlocal lat, lon
            if lat is None or lon is None:
                coords = feedr_service.geocode_city(city)
                if coords is None:
                    _job_set(jid, {"status": "error", "result": None,
                                   "error": f"Could not geocode city '{city}'"})
                    return
                lat, lon = coords

            vendors = feedr_service.fetch_feedr_vendors(lat, lon, city, max_vendors=300)
            if not vendors:
                _job_set(jid, {
                    "status": "done",
                    "result": {"vendors": 0, "chunks": 0, "collection_name": "",
                               "warning": "No vendors returned from Feedr.co API for this location."},
                    "error": None,
                })
                return

            source_id, error = feedr_service.index_feedr_vendors(
                db, user_id, vendors, city, replace_existing
            )
            if error:
                _job_set(jid, {"status": "error", "result": None, "error": error})
                return

            col = f"feedr_u{user_id}_{city.lower().strip().replace(' ', '_')[:30]}"
            _job_set(jid, {
                "status": "done",
                "result": {
                    "vendors": len(vendors),
                    "chunks": len(vendors) * 2,
                    "collection_name": col,
                    "source_id": source_id,
                    "city": city,
                    "sample": [v["name"] for v in vendors[:5]],
                },
                "error": None,
            })
        except Exception as exc:
            _job_set(jid, {"status": "error", "result": None, "error": str(exc)})
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": jid, "status": "running"}


@router.post("/parse-catering-file")
async def parse_catering_file(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    """
    Upload a PDF/DOCX/TXT containing catering requirements.
    Extracts text and uses LLM to parse dietary groups, headcount, budget, and location.
    """
    from app.services import rag_service

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "docx", "txt"):
        raise HTTPException(status_code=422, detail="Only PDF, DOCX, and TXT files are supported")

    file_bytes = await file.read()
    try:
        if ext == "pdf":
            text = indexing_service.extract_text_from_pdf(file_bytes)
        elif ext == "docx":
            text = indexing_service.extract_text_from_docx(file_bytes)
        else:
            text = file_bytes.decode("utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from the uploaded file")

    parsed = rag_service.parse_catering_requirements(text)
    return {
        "groups": parsed.get("groups") or [],
        "total_headcount": parsed.get("total_headcount") or 0,
        "budget": parsed.get("budget"),
        "location": parsed.get("location"),
        "raw_text_preview": text[:500],
    }


@router.post("/catering-match")
def catering_match(
    body: CateringMatchRequest,
    _: User = Depends(get_current_user),
):
    """
    Find nearby Feedr.co catering vendors matched to dietary groups.
    Returns per-group vendor matches with estimated costs (headcount × price/head).
    """
    from app.services import feedr_service

    groups_dicts = [g.dict() for g in body.groups]

    try:
        result = feedr_service.match_vendors_for_groups(body.lat, body.lon, groups_dicts)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Budget summary
    if body.budget is not None:
        min_costs = []
        for grp in result["groups"]:
            priced = [v["estimated_cost"] for v in grp["matched_vendors"] if v.get("estimated_cost") is not None]
            if priced:
                min_costs.append(min(priced))
        if min_costs:
            min_total = round(sum(min_costs), 2)
            result["budget_summary"] = {
                "total_budget": body.budget,
                "min_estimated_total": min_total,
                "within_budget": min_total <= body.budget,
                "over_by": round(max(0.0, min_total - body.budget), 2),
                "remaining": round(max(0.0, body.budget - min_total), 2),
            }
        else:
            result["budget_summary"] = {
                "total_budget": body.budget,
                "min_estimated_total": None,
                "within_budget": None,
                "over_by": None,
                "remaining": None,
            }
    else:
        result["budget_summary"] = None

    return result


@router.get("/vendors/detail/{permalink}")
def get_vendor_detail(
    permalink: str,
    _: User = Depends(get_current_user),
):
    """
    Fetch full vendor details + complete live menu from Feedr.co via CaterDesk GraphQL.
    Returns vendor info, gallery images, and all menu items grouped by meal tag.
    """
    from app.services import feedr_service

    try:
        detail = feedr_service.fetch_vendor_detail(permalink)
        return detail
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/vendors/nearby")
def get_nearby_vendors(
    lat: float,
    lon: float,
    max_results: int = 20,
    _: User = Depends(get_current_user),
):
    """
    Return catering vendors near (lat, lon) from Feedr.co, sorted by distance (km).
    max_results: 1–100.
    """
    from app.services import feedr_service

    if not (1 <= max_results <= 100):
        raise HTTPException(status_code=422, detail="max_results must be between 1 and 100")

    try:
        vendors = feedr_service.find_nearby_vendors(lat, lon, max_results=max_results)
        return {"vendors": vendors, "count": len(vendors), "lat": lat, "lon": lon}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/feedr/{job_id}")
def feedr_job_status(
    job_id: str,
    _: User = Depends(get_current_user),
):
    """Poll feedr scraping job status."""
    job = _job_get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/sources")
def list_sources(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return indexing_service.get_user_sources(db, current_user.id)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ok, error = indexing_service.delete_source(db, source_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)


@router.get("/collections")
def all_collections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"collections": indexing_service.get_all_user_collections(db, current_user.id)}
