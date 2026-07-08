from typing import List

from sqlalchemy.orm import Session

from app.models.ai_chat import AIChatMessage


def save_message(db: Session, user_id: int, role: str, content: str) -> None:
    db.add(AIChatMessage(user_id=user_id, role=role, content=content))
    db.commit()


def get_history(db: Session, user_id: int, limit: int = 50) -> List[dict]:
    messages = (
        db.query(AIChatMessage)
        .filter(AIChatMessage.user_id == user_id)
        .order_by(AIChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    return [_to_dict(m) for m in messages]


def clear_history(db: Session, user_id: int) -> None:
    db.query(AIChatMessage).filter(AIChatMessage.user_id == user_id).delete()
    db.commit()


def _to_dict(m: AIChatMessage) -> dict:
    return {
        "role": m.role,
        "content": m.content,
        "created_at": m.created_at,
    }
