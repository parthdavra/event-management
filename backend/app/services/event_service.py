from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.event import Event
from app.schemas.event import EventCreate, EventOut, EventUpdate


def create_event(
    db: Session, user_id: int, body: EventCreate
) -> Tuple[Optional[Event], Optional[str]]:
    try:
        event = Event(
            user_id=user_id,
            title=body.title,
            description=body.description,
            date_time=body.date_time,
            location=body.location,
            category=body.category,
            is_shared=body.is_shared,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event, None
    except Exception as exc:
        db.rollback()
        return None, str(exc)


def get_user_events(
    db: Session,
    user_id: int,
    category_filter: Optional[str] = None,
    sort_by: str = "date_time",
) -> List[Event]:
    q = db.query(Event).filter(Event.user_id == user_id)
    if category_filter:
        q = q.filter(Event.category == category_filter)
    if sort_by == "title":
        q = q.order_by(Event.title)
    elif sort_by == "created_at":
        q = q.order_by(Event.created_at.desc())
    else:
        q = q.order_by(Event.date_time.desc())
    return q.all()


def get_shared_events(db: Session) -> List[Event]:
    return (
        db.query(Event)
        .filter(Event.is_shared.is_(True))
        .order_by(Event.date_time.desc())
        .all()
    )


def get_event(db: Session, event_id: int) -> Optional[Event]:
    return db.query(Event).filter(Event.id == event_id).first()


def update_event(
    db: Session, event_id: int, user_id: int, body: EventUpdate
) -> Tuple[Optional[Event], Optional[str]]:
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user_id).first()
    if not event:
        return None, "Event not found or not authorized."
    try:
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(event, field, value)
        event.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(event)
        return event, None
    except Exception as exc:
        db.rollback()
        return None, str(exc)


def delete_event(
    db: Session, event_id: int, user_id: int
) -> Tuple[bool, Optional[str]]:
    event = db.query(Event).filter(Event.id == event_id, Event.user_id == user_id).first()
    if not event:
        return False, "Event not found or not authorized."
    try:
        db.delete(event)
        db.commit()
        return True, None
    except Exception as exc:
        db.rollback()
        return False, str(exc)
