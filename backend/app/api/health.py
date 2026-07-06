import socket
import time

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine

router = APIRouter(prefix="/health", tags=["health"])

settings = get_settings()


def _check_postgres() -> dict:
    t0 = time.time()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version()")).fetchone()
        return {"ok": True, "detail": row[0].split(",")[0], "ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "ms": round((time.time() - t0) * 1000)}


def _check_chromadb() -> dict:
    t0 = time.time()
    try:
        import chromadb
        if settings.chroma_use_http:
            client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        else:
            client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        cols = client.list_collections()
        return {"ok": True, "detail": f"{len(cols)} collection(s)", "ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "ms": round((time.time() - t0) * 1000)}


def _check_openai() -> dict:
    t0 = time.time()
    if not settings.is_openai_configured():
        return {"ok": False, "detail": "Azure OpenAI keys not configured", "ms": 0}
    try:
        host = settings.azure_openai_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        socket.gethostbyname(host)
        return {"ok": True, "detail": f"DNS resolved for {host}", "ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "ms": round((time.time() - t0) * 1000)}


def _check_geoapify() -> dict:
    t0 = time.time()
    if not settings.geoapify_api_key:
        return {"ok": False, "detail": "GEOAPIFY_API_KEY not set", "ms": 0}
    try:
        import requests
        r = requests.get(
            "https://api.geoapify.com/v1/geocode/search",
            params={"text": "London", "type": "city", "limit": 1, "apiKey": settings.geoapify_api_key},
            timeout=8,
        )
        r.raise_for_status()
        return {"ok": True, "detail": "API key valid", "ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "ms": round((time.time() - t0) * 1000)}


@router.get("/")
def health_check():
    """Full health check — checks all services."""
    checks = {
        "postgres": _check_postgres(),
        "chromadb": _check_chromadb(),
        "azure_openai": _check_openai(),
        "geoapify": _check_geoapify(),
    }
    all_ok = all(c["ok"] for c in checks.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "checks": checks,
    }


@router.get("/ping")
def ping():
    """Lightweight liveness probe."""
    return {"status": "ok"}
