from datetime import datetime
from .database import SessionLocal, Event


def create_event(user_id: int, title: str, description: str, date_time: datetime,
                 location: str, category: str, is_shared: bool = False):
    db = SessionLocal()
    try:
        event = Event(
            user_id=user_id, title=title, description=description,
            date_time=date_time, location=location, category=category,
            is_shared=is_shared,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return _to_dict(event), None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def get_user_events(user_id: int, category_filter: str = None, sort_by: str = "date_time"):
    db = SessionLocal()
    try:
        q = db.query(Event).filter(Event.user_id == user_id)
        if category_filter:
            q = q.filter(Event.category == category_filter)
        if sort_by == "title":
            q = q.order_by(Event.title)
        elif sort_by == "created_at":
            q = q.order_by(Event.created_at.desc())
        else:
            q = q.order_by(Event.date_time.desc())
        return [_to_dict(e) for e in q.all()]
    finally:
        db.close()


def get_shared_events():
    db = SessionLocal()
    try:
        events = (
            db.query(Event)
            .filter(Event.is_shared == True)
            .order_by(Event.date_time.desc())
            .all()
        )
        return [_to_dict(e) for e in events]
    finally:
        db.close()


def get_event(event_id: int):
    db = SessionLocal()
    try:
        e = db.query(Event).filter(Event.id == event_id).first()
        return _to_dict(e) if e else None
    finally:
        db.close()


def update_event(event_id: int, user_id: int, **kwargs):
    db = SessionLocal()
    try:
        event = db.query(Event).filter(
            Event.id == event_id, Event.user_id == user_id
        ).first()
        if not event:
            return False, "Event not found or not authorized."
        for key, val in kwargs.items():
            if hasattr(event, key):
                setattr(event, key, val)
        event.updated_at = datetime.utcnow()
        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def delete_event(event_id: int, user_id: int):
    db = SessionLocal()
    try:
        event = db.query(Event).filter(
            Event.id == event_id, Event.user_id == user_id
        ).first()
        if not event:
            return False, "Event not found or not authorized."
        db.delete(event)
        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def _to_dict(e: Event) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "title": e.title,
        "description": e.description,
        "date_time": e.date_time,
        "location": e.location,
        "category": e.category,
        "is_shared": e.is_shared,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }
