from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db
from app.api import auth, events, chat, venues, ai, indexing, health, mcp

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialise DB tables
    init_db()
    yield
    # Shutdown: nothing to clean up (connections pooled by SQLAlchemy)


app = FastAPI(
    title=settings.app_name,
    description="Production-grade AI Event Management REST API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow the Streamlit frontend (port 8501) and localhost dev servers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://frontend:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(chat.router)
app.include_router(venues.router)
app.include_router(ai.router)
app.include_router(indexing.router)
app.include_router(mcp.router)


@app.get("/")
def root():
    return {
        "service": settings.app_name,
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
    }
