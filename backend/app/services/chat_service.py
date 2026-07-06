from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage
from app.models.user import User


def send_message(
    db: Session,
    user_id: int,
    message: str,
    event_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    try:
        msg = ChatMessage(user_id=user_id, event_id=event_id, message=message)
        db.add(msg)
        db.commit()
        db.refresh(msg)
        # Fetch username for the response
        user = db.query(User).filter(User.id == user_id).first()
        return _to_dict(msg, user.username if user else "unknown"), None
    except Exception as exc:
        db.rollback()
        return None, str(exc)


def get_messages(
    db: Session,
    event_id: Optional[int] = None,
    limit: int = 100,
) -> List[dict]:
    q = (
        db.query(ChatMessage, User.username)
        .join(User, ChatMessage.user_id == User.id)
    )
    if event_id is not None:
        q = q.filter(ChatMessage.event_id == event_id)
    else:
        q = q.filter(ChatMessage.event_id.is_(None))
    results = q.order_by(ChatMessage.created_at.asc()).limit(limit).all()
    return [_to_dict(msg, username) for msg, username in results]


def _to_dict(msg: ChatMessage, username: str) -> dict:
    return {
        "id": msg.id,
        "username": username,
        "message": msg.message,
        "event_id": msg.event_id,
        "created_at": msg.created_at,
    }
