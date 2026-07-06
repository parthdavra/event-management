from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    date_time: datetime
    location: Optional[str] = None
    category: Optional[str] = None
    is_shared: bool = False


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    date_time: Optional[datetime] = None
    location: Optional[str] = None
    category: Optional[str] = None
    is_shared: Optional[bool] = None


class EventOut(BaseModel):
    id: int
    user_id: int
    title: str
    description: Optional[str] = None
    date_time: datetime
    location: Optional[str] = None
    category: Optional[str] = None
    is_shared: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
