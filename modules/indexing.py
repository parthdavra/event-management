import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .database import SessionLocal, IndexedSource
from .rag import add_to_collection, delete_collection


# ── Text utilities ─────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 20]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    import io
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text_from_docx(file_bytes: bytes) -> str:
    import io
    import docx
    doc = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ── Collection name helpers ────────────────────────────────────────────────────

def _make_collection_name(user_id: int) -> str:
    """Unique collection name for a file/text source."""
    return f"u{user_id}x{uuid.uuid4().hex[:12]}"


def _city_slug(city: str) -> str:
    """Convert a city name to a ChromaDB-safe slug (max 30 chars)."""
    slug = re.sub(r"[^a-z0-9]", "_", city.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:30]


def get_city_collection_name(user_id: int, city: str) -> str:
    """Deterministic collection name for a user + city combination."""
    return f"city_u{user_id}_{_city_slug(city)}"


# ── File / text indexing ───────────────────────────────────────────────────────

def index_source(
    user_id: int, source_name: str, source_type: str, text: str
) -> Tuple[Optional[int], Optional[str]]:
    """Chunk raw text, embed, and store in a new ChromaDB collection."""
    chunks = chunk_text(text)
    if not chunks:
        return None, "No indexable text content found."

    collection_name = _make_collection_name(user_id)
    db = SessionLocal()
    source_id: Optional[int] = None
    try:
        source = IndexedSource(
            user_id=user_id,
            source_name=source_name,
            source_type=source_type,
            chunk_count=0,
            status="pending",
            collection_name=collection_name,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        metadatas = [
            {"source": source_name, "chunk_index": i, "source_id": str(source_id)}
            for i in range(len(chunks))
        ]
        ids = [f"{collection_name}_{i}" for i in range(len(chunks))]

        count = add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as e:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(e)
    finally:
        db.close()


# ── City API indexing ──────────────────────────────────────────────────────────

def index_city_data(
    user_id: int,
    city: str,
    venues: List[Dict],
    replace_existing: bool = True,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Convert venue dicts to text chunks and index them in a city-named collection.
    One collection per user+city; set replace_existing=True to refresh stale data.
    """
    from .api_fetcher import venue_to_text

    if not venues:
        return None, "No venues provided."

    collection_name = get_city_collection_name(user_id, city)
    source_name = f"{city.title()} — City API Data"

    db = SessionLocal()
    source_id: Optional[int] = None
    try:
        # Replace existing city collection if requested
        if replace_existing:
            existing = (
                db.query(IndexedSource)
                .filter(
                    IndexedSource.user_id == user_id,
                    IndexedSource.collection_name == collection_name,
                )
                .first()
            )
            if existing:
                delete_collection(existing.collection_name)
                db.delete(existing)
                db.commit()

        # Build text chunks — one chunk per venue
        chunks = [venue_to_text(v, city) for v in venues]
        metadatas = [
            {
                "source": source_name,
                "city": city,
                "chunk_index": i,
                "venue_name": v.get("name", ""),
                "venue_type": v.get("type", ""),
                "api_source": v.get("source", ""),
                "capacity": str(v.get("capacity", "")),
                "phone": str(v.get("phone", "")),
                "email": str(v.get("email", "")),
                "website": str(v.get("website", "")),
                "lat": str(v.get("lat", "")),
                "lon": str(v.get("lon", "")),
            }
            for i, v in enumerate(venues)
        ]
        ids = [f"{collection_name}_{i}" for i in range(len(chunks))]

        # DB record
        source = IndexedSource(
            user_id=user_id,
            source_name=source_name,
            source_type="city_api",
            chunk_count=0,
            status="pending",
            collection_name=collection_name,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        count = add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as e:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(e)
    finally:
        db.close()


# ── Smart event plan indexing ─────────────────────────────────────────────────

def index_event_plan(
    user_id: int,
    event_name: str,
    collection_slug: str,
    document_text: str,
    venues: List[Dict],
    city: str,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Index both the event requirements document and fetched venue data into
    a single named ChromaDB collection so the AI can answer questions across both.
    """
    from .api_fetcher import venue_to_text

    safe_slug = re.sub(r"[^a-z0-9]", "_", collection_slug.lower())[:20].strip("_")
    collection_name = f"evp_u{user_id}_{safe_slug}"

    doc_chunks = chunk_text(document_text) if document_text.strip() else []
    venue_chunks = [venue_to_text(v, city) for v in venues]
    all_chunks = doc_chunks + venue_chunks

    if not all_chunks:
        return None, "No content to index."

    doc_metas = [
        {"source": event_name, "chunk_type": "requirements", "chunk_index": i, "source_id": ""}
        for i in range(len(doc_chunks))
    ]
    venue_metas = [
        {
            "source": f"{city} venues",
            "chunk_type": "venue",
            "city": city,
            "chunk_index": i,
            "venue_name": v.get("name", ""),
            "venue_type": v.get("type", ""),
            "api_source": v.get("source", ""),
            "capacity": str(v.get("capacity", "")),
            "phone": str(v.get("phone", "")),
            "email": str(v.get("email", "")),
            "website": str(v.get("website", "")),
            "lat": str(v.get("lat", "")),
            "lon": str(v.get("lon", "")),
        }
        for i, v in enumerate(venues)
    ]
    all_metas = doc_metas + venue_metas
    ids = [f"{collection_name}_{i}" for i in range(len(all_chunks))]

    db = SessionLocal()
    source_id: Optional[int] = None
    try:
        existing = (
            db.query(IndexedSource)
            .filter(
                IndexedSource.user_id == user_id,
                IndexedSource.collection_name == collection_name,
            )
            .first()
        )
        if existing:
            delete_collection(existing.collection_name)
            db.delete(existing)
            db.commit()

        source = IndexedSource(
            user_id=user_id,
            source_name=event_name,
            source_type="event_plan",
            chunk_count=0,
            status="pending",
            collection_name=collection_name,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        count = add_to_collection(collection_name, all_chunks, all_metas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as e:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(e)
    finally:
        db.close()


# ── Query helpers ──────────────────────────────────────────────────────────────

def get_user_sources(user_id: int) -> List[dict]:
    db = SessionLocal()
    try:
        sources = (
            db.query(IndexedSource)
            .filter(IndexedSource.user_id == user_id)
            .order_by(IndexedSource.indexed_at.desc())
            .all()
        )
        return [_src_to_dict(s) for s in sources]
    finally:
        db.close()


def get_user_cities(user_id: int) -> List[dict]:
    """Return only city-API indexed sources for the user."""
    db = SessionLocal()
    try:
        sources = (
            db.query(IndexedSource)
            .filter(
                IndexedSource.user_id == user_id,
                IndexedSource.source_type == "city_api",
                IndexedSource.status == "indexed",
            )
            .order_by(IndexedSource.indexed_at.desc())
            .all()
        )
        return [_src_to_dict(s) for s in sources]
    finally:
        db.close()


def delete_source(source_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
    db = SessionLocal()
    try:
        source = db.query(IndexedSource).filter(
            IndexedSource.id == source_id, IndexedSource.user_id == user_id
        ).first()
        if not source:
            return False, "Source not found or not authorized."
        collection_name = source.collection_name
        db.delete(source)
        db.commit()
        delete_collection(collection_name)
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def get_all_user_collections(user_id: int) -> List[str]:
    db = SessionLocal()
    try:
        sources = (
            db.query(IndexedSource)
            .filter(IndexedSource.user_id == user_id, IndexedSource.status == "indexed")
            .all()
        )
        return [s.collection_name for s in sources]
    finally:
        db.close()


def _src_to_dict(s: IndexedSource) -> dict:
    return {
        "id": s.id,
        "source_name": s.source_name,
        "source_type": s.source_type,
        "chunk_count": s.chunk_count,
        "indexed_at": s.indexed_at,
        "status": s.status,
        "collection_name": s.collection_name,
    }
