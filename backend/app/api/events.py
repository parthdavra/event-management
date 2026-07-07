import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.event import EventBriefRequest, EventCreate, EventOut, EventUpdate
from app.services import rag_service
from app.services.event_service import (
    create_event,
    delete_event,
    get_event,
    get_shared_events,
    get_user_events,
    update_event,
)

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create(
    body: EventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event, error = create_event(db, current_user.id, body)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return event


@router.get("/", response_model=List[EventOut])
def list_mine(
    category: Optional[str] = Query(None),
    sort_by: str = Query("date_time", pattern="^(date_time|title|created_at)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_user_events(db, current_user.id, category_filter=category, sort_by=sort_by)


@router.get("/shared", response_model=List[EventOut])
def list_shared(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_shared_events(db)


@router.get("/{event_id}", response_model=EventOut)
def get_one(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if not event.is_shared and event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return event


@router.patch("/{event_id}", response_model=EventOut)
def update(
    event_id: int,
    body: EventUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event, error = update_event(db, event_id, current_user.id, body)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ok, error = delete_event(db, event_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)


@router.post("/{event_id}/brief")
def save_brief(
    event_id: int,
    body: EventBriefRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Extract AI planning requirements from text and store in event.brief_json."""
    event = get_event(db, event_id)
    if event is None or event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    try:
        extracted = rag_service.extract_event_requirements(body.text)
        event.brief_json = json.dumps(extracted)
        db.commit()
        return extracted
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/{event_id}/catering-brief")
def save_catering_brief(
    event_id: int,
    body: EventBriefRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Extract AI catering requirements from text and store in event.catering_json."""
    event = get_event(db, event_id)
    if event is None or event.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    try:
        extracted = rag_service.parse_catering_requirements(body.text)
        event.catering_json = json.dumps(extracted)
        db.commit()
        return extracted
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
