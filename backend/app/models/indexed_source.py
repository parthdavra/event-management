from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.core.database import Base


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
