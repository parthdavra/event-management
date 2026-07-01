import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    DateTime, Boolean, ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:P%40rth%23123@localhost:5432/event_mgmt")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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

    owner = relationship("User", back_populates="events")
    messages = relationship("ChatMessage", back_populates="event", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    author = relationship("User", back_populates="messages")
    event = relationship("Event", back_populates="messages")


class IndexedSource(Base):
    __tablename__ = "indexed_sources"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    source_name = Column(String(300), nullable=False)
    source_type = Column(String(50))
    chunk_count = Column(Integer, default=0)
    indexed_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending")  # pending | indexed | failed
    collection_name = Column(String(100))

    owner = relationship("User", back_populates="indexed_sources")


def init_db():
    Base.metadata.create_all(bind=engine)
