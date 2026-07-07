from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    date_time = Column(DateTime, nullable=False)
    location = Column(String(300))
    category = Column(String(50))
    is_shared = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    # AI-extracted planning requirements (JSON string from ai_extract_requirements)
    brief_json = Column(Text, nullable=True)
    # AI-extracted catering requirements (JSON string from parse_catering_requirements)
    catering_json = Column(Text, nullable=True)

    owner = relationship("User", back_populates="events")
    messages = relationship("ChatMessage", back_populates="event", cascade="all, delete-orphan")
