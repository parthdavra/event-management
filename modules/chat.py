from .database import SessionLocal, ChatMessage, User


def send_message(user_id: int, message: str, event_id: int = None):
    db = SessionLocal()
    try:
        msg = ChatMessage(user_id=user_id, event_id=event_id, message=message)
        db.add(msg)
        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def get_messages(event_id: int = None, limit: int = 100):
    db = SessionLocal()
    try:
        q = (
            db.query(ChatMessage, User.username)
            .join(User, ChatMessage.user_id == User.id)
        )
        if event_id is not None:
            q = q.filter(ChatMessage.event_id == event_id)
        else:
            q = q.filter(ChatMessage.event_id == None)  # noqa: E711
        results = q.order_by(ChatMessage.created_at.asc()).limit(limit).all()
        return [
            {
                "id": msg.id,
                "username": username,
                "message": msg.message,
                "created_at": msg.created_at,
            }
            for msg, username in results
        ]
    finally:
        db.close()
