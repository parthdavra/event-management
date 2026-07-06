from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MessageCreate(BaseModel):
    message: str
    event_id: Optional[int] = None


class MessageOut(BaseModel):
    id: int
    username: str
    message: str
    event_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}
