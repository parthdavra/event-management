from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.chat import MessageCreate, MessageOut
from app.services.chat_service import get_messages, send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/", response_model=List[MessageOut])
def list_messages(
    event_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_messages(db, event_id=event_id, limit=limit)


@router.post("/", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def post_message(
    body: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg, error = send_message(db, current_user.id, body.message, body.event_id)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    return msg
