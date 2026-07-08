from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create all tables if they do not exist."""
    # Import models so SQLAlchemy registers them before create_all
    from app.models import user, event, chat, indexed_source, ai_chat, query_metric  # noqa: F401
    Base.metadata.create_all(bind=engine)
