from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    events = relationship("Event", back_populates="owner", cascade="all, delete-orphan")
    messages = relationship("ChatMessage", back_populates="author", cascade="all, delete-orphan")
    indexed_sources = relationship("IndexedSource", back_populates="owner", cascade="all, delete-orphan")
