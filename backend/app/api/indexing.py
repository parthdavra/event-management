from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services import indexing_service

router = APIRouter(prefix="/indexing", tags=["indexing"])


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
