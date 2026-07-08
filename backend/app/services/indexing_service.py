"""
Indexing service — chunks text, embeds, stores in OpenSearch, records in DB.
All functions accept a SQLAlchemy Session (injected via FastAPI deps).
"""

import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.indexed_source import IndexedSource
from app.services import rag_service


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
    return f"u{user_id}x{uuid.uuid4().hex[:12]}"


def _city_slug(city: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", city.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:30]


def _event_type_slug(event_type: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", event_type.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:30]


def get_city_collection_name(user_id: int, city: str) -> str:
    return f"city_u{user_id}_{_city_slug(city)}"


def get_event_type_collection_name(user_id: int, event_type: str) -> str:
    return f"evt_u{user_id}_{_event_type_slug(event_type)}"


# ── File / text indexing ───────────────────────────────────────────────────────

def index_source(
    db: Session,
    user_id: int,
    source_name: str,
    source_type: str,
    text: str,
) -> Tuple[Optional[int], Optional[str]]:
    chunks = chunk_text(text)
    if not chunks:
        return None, "No indexable text content found."

    collection_name = _make_collection_name(user_id)
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

        count = rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as exc:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(exc)


# ── City API indexing ──────────────────────────────────────────────────────────

def index_city_data(
    db: Session,
    user_id: int,
    city: str,
    venues: List[Dict],
    replace_existing: bool = True,
) -> Tuple[Optional[int], Optional[str]]:
    from app.services.venue_service import venue_to_text

    if not venues:
        return None, "No venues provided."

    collection_name = get_city_collection_name(user_id, city)
    source_name = f"{city.title()} — City API Data"

    source_id: Optional[int] = None
    try:
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
                rag_service.delete_collection(existing.collection_name)
                db.delete(existing)
                db.commit()

        chunks = [venue_to_text(v, city) for v in venues]
        metadatas = [
            {
                "source": source_name,
                "city": city,
                "chunk_index": i,
                "chunk_type": "venue",
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

        count = rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as exc:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(exc)


# ── Smart event plan indexing ─────────────────────────────────────────────────

def index_event_plan(
    db: Session,
    user_id: int,
    event_name: str,
    collection_slug: str,
    document_text: str,
    venues: List[Dict],
    city: str,
) -> Tuple[Optional[int], Optional[str]]:
    from app.services.venue_service import venue_to_text

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
            rag_service.delete_collection(existing.collection_name)
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

        count = rag_service.add_to_collection(collection_name, all_chunks, all_metas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as exc:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(exc)


# ── Event-type collection indexing ────────────────────────────────────────────

def index_event_type_venues(
    db: Session,
    user_id: int,
    event_type: str,
    city: str,
    venues: List[Dict],
    replace_existing: bool = True,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Index venues into a per-event-type collection.

    Collection naming: evt_u{user_id}_{event_type_slug}
    Each venue produces two chunks:
      1. Rich text (human-readable, used for semantic search)
      2. Raw JSON (exact data, tagged chunk_type='raw_json')
    """
    import json as _json
    from app.services.venue_service import venue_to_text

    if not venues:
        return None, "No venues provided."

    collection_name = get_event_type_collection_name(user_id, event_type)
    source_name = f"{event_type.title()} — {city.title()} Venues"

    source_id: Optional[int] = None
    try:
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
                rag_service.delete_collection(existing.collection_name)
                db.delete(existing)
                db.commit()

        chunks: List[str] = []
        metadatas: List[dict] = []

        for i, v in enumerate(venues):
            # Chunk 1: rich text for semantic search
            rich_text = venue_to_text(v, city)
            chunks.append(rich_text)
            metadatas.append({
                "source": source_name,
                "event_type": event_type,
                "city": city,
                "chunk_type": "venue",
                "chunk_index": i,
                "venue_name": v.get("name", ""),
                "venue_type": v.get("type", ""),
                "api_source": v.get("source", ""),
                "capacity": str(v.get("capacity", "")),
                "phone": str(v.get("phone", "")),
                "website": str(v.get("website", "")),
                "has_canvas_data": str(bool(v.get("canvas_price_guide") or v.get("canvas_features"))),
                "canvas_from_price": str((v.get("canvas_price_guide") or {}).get("from_price", "")),
                "lat": str(v.get("lat", "")),
                "lon": str(v.get("lon", "")),
            })

            # Chunk 2: raw JSON for exact data retrieval
            raw_text = f"RAW JSON for {v.get('name', 'venue')} ({city}):\n" + _json.dumps(v, ensure_ascii=False)
            chunks.append(raw_text)
            metadatas.append({
                "source": source_name,
                "event_type": event_type,
                "city": city,
                "chunk_type": "raw_json",
                "chunk_index": i,
                "venue_name": v.get("name", ""),
                "api_source": v.get("source", ""),
            })

        ids = [f"{collection_name}_{i}" for i in range(len(chunks))]

        source = IndexedSource(
            user_id=user_id,
            source_name=source_name,
            source_type="event_type",
            chunk_count=0,
            status="pending",
            collection_name=collection_name,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        count = rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as exc:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(exc)


# ── Bulk event-type indexing ──────────────────────────────────────────────────

def bulk_index_event_types(
    db: Session,
    user_id: int,
    cities: List[str],
    event_types: List[str],
    categories: List[str],
    radius_km: int = 5,
    max_venues_per_city: int = 300,
    replace_existing: bool = True,
) -> Dict:
    """
    One-shot bulk indexer:
      1. Fetch all venues for each city (Canvas cache = single HTTP call per city)
      2. De-duplicate across cities
      3. For every event type: mark matches, sort matched-first, index 2 chunks/venue
      4. Return full stats dict

    Returns:
      {
        total_collections, total_venues, total_chunks,
        cities_fetched, errors,
        collections: [{event_type, venues, chunks, collection_name, source_id}]
      }
    """
    import json as _json
    from app.services.canvas_service import venue_matches_event_type
    from app.services.venue_service import fetch_all_city_venues, venue_to_text

    stats: Dict = {
        "total_collections": 0,
        "total_venues": 0,
        "total_chunks": 0,
        "cities_fetched": [],
        "errors": [],
        "collections": [],
    }

    # ── Step 1: fetch venues for every city ───────────────────────────────────
    all_venues_by_city: Dict[str, List[Dict]] = {}
    for city in cities:
        try:
            venues, _, _ = fetch_all_city_venues(
                city,
                categories,
                radius_km=radius_km,
                use_foursquare=True,
                use_geoapify=True,
                enrich_details=True,
                max_venues=max_venues_per_city,
            )
            all_venues_by_city[city] = venues
            stats["cities_fetched"].append({"city": city, "venues": len(venues)})
        except Exception as exc:
            stats["errors"].append(f"City '{city}': {exc}")
            all_venues_by_city[city] = []

    # ── Step 2: merge + de-duplicate across cities ────────────────────────────
    merged: List[Dict] = []
    seen: set = set()
    for city, venues in all_venues_by_city.items():
        for v in venues:
            key = (
                (v.get("name") or "").lower().strip(),
                round(float(v.get("lat") or 0), 3),
                round(float(v.get("lon") or 0), 3),
            )
            if key not in seen:
                seen.add(key)
                merged.append({**v, "_city": city})

    if not merged:
        stats["errors"].append("No venues found across all selected cities.")
        return stats

    # ── Step 3: for each event type, build + index a collection ───────────────
    city_label = " / ".join(c.title() for c in cities)

    for event_type in event_types:
        collection_name = get_event_type_collection_name(user_id, event_type)
        source_name = f"{event_type.title()} — {city_label} Venues"

        # Tag event_type_match for every venue
        tagged: List[Dict] = []
        for v in merged:
            v_copy = dict(v)
            et_list = v.get("event_types") or []
            pf_list = v.get("canvas_perfect_for") or []
            # Match via Canvas event_types list, perfect_for, or keyword heuristic
            match = (
                venue_matches_event_type(et_list, event_type)
                or any(event_type.lower() in p.lower() for p in pf_list)
            )
            v_copy["event_type_match"] = match
            tagged.append(v_copy)

        # Sort: matched venues first, then rest
        tagged.sort(key=lambda v: (0 if v.get("event_type_match") else 1, v.get("name", "")))

        # Build chunks
        chunks: List[str] = []
        metadatas: List[dict] = []
        city_for_chunk = tagged[0].get("_city", city_label) if tagged else city_label

        for i, v in enumerate(tagged):
            v_city = v.get("_city", city_for_chunk)
            # Chunk 1: rich semantic text
            chunks.append(venue_to_text(v, v_city))
            metadatas.append({
                "source": source_name,
                "event_type": event_type,
                "city": v_city,
                "chunk_type": "venue",
                "chunk_index": i,
                "venue_name": v.get("name", ""),
                "venue_type": v.get("type", ""),
                "api_source": v.get("source", ""),
                "capacity": str(v.get("capacity", "")),
                "event_type_match": str(v.get("event_type_match", False)),
                "has_canvas_data": str(bool(v.get("canvas_price_guide") or v.get("canvas_features"))),
                "canvas_from_price": str((v.get("canvas_price_guide") or {}).get("from_price", "")),
                "lat": str(v.get("lat", "")),
                "lon": str(v.get("lon", "")),
            })
            # Chunk 2: raw JSON
            chunks.append(
                f"RAW JSON for {v.get('name', 'venue')} ({v_city}):\n"
                + _json.dumps({k: val for k, val in v.items() if k != "_city"}, ensure_ascii=False)
            )
            metadatas.append({
                "source": source_name,
                "event_type": event_type,
                "city": v_city,
                "chunk_type": "raw_json",
                "chunk_index": i,
                "venue_name": v.get("name", ""),
                "api_source": v.get("source", ""),
            })

        ids = [f"{collection_name}_{i}" for i in range(len(chunks))]
        source_id: Optional[int] = None

        try:
            # Remove existing collection if replacing
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
                    rag_service.delete_collection(existing.collection_name)
                    db.delete(existing)
                    db.commit()

            source = IndexedSource(
                user_id=user_id,
                source_name=source_name,
                source_type="event_type",
                chunk_count=0,
                status="pending",
                collection_name=collection_name,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            source_id = source.id

            count = rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

            source.chunk_count = count
            source.status = "indexed"
            source.indexed_at = datetime.utcnow()
            db.commit()

            matched_count = sum(1 for v in tagged if v.get("event_type_match"))
            stats["total_collections"] += 1
            stats["total_venues"] += len(tagged)
            stats["total_chunks"] += count
            stats["collections"].append({
                "event_type": event_type,
                "collection_name": collection_name,
                "source_id": source_id,
                "total_venues": len(tagged),
                "matched_venues": matched_count,
                "chunks": count,
                "status": "indexed",
            })

        except Exception as exc:
            db.rollback()
            if source_id is not None:
                try:
                    src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                    if src:
                        src.status = "failed"
                        db.commit()
                except Exception:
                    pass
            stats["errors"].append(f"Event type '{event_type}': {exc}")
            stats["collections"].append({
                "event_type": event_type,
                "collection_name": collection_name,
                "status": "failed",
                "error": str(exc),
                "total_venues": 0,
                "chunks": 0,
            })

    return stats


# ── Query helpers ──────────────────────────────────────────────────────────────

def get_user_sources(db: Session, user_id: int) -> List[dict]:
    sources = (
        db.query(IndexedSource)
        .filter(IndexedSource.user_id == user_id)
        .order_by(IndexedSource.indexed_at.desc())
        .all()
    )
    return [_src_to_dict(s) for s in sources]


def get_all_user_collections(db: Session, user_id: int) -> List[str]:
    sources = (
        db.query(IndexedSource)
        .filter(IndexedSource.user_id == user_id, IndexedSource.status == "indexed")
        .all()
    )
    return [s.collection_name for s in sources]


def delete_source(
    db: Session, source_id: int, user_id: int
) -> Tuple[bool, Optional[str]]:
    source = db.query(IndexedSource).filter(
        IndexedSource.id == source_id, IndexedSource.user_id == user_id
    ).first()
    if not source:
        return False, "Source not found or not authorized."
    collection_name = source.collection_name
    try:
        db.delete(source)
        db.commit()
        rag_service.delete_collection(collection_name)
        return True, None
    except Exception as exc:
        db.rollback()
        return False, str(exc)


def _src_to_dict(s: IndexedSource) -> dict:
    return {
        "id": s.id,
        "source_name": s.source_name,
        "source_type": s.source_type,
        "chunk_count": s.chunk_count,
        "indexed_at": s.indexed_at.isoformat() if s.indexed_at else None,
        "status": s.status,
        "collection_name": s.collection_name,
    }
