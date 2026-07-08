"""
Indexes the fixed golden-dataset venue fixture into dedicated OpenSearch
collections, using the exact same two-chunk-per-venue shape
`indexing_service.index_event_type_venues` uses for real venue indexing
(rich-text chunk + raw-JSON chunk), so retrieval/filtering behaves identically
to production. Bypasses the `IndexedSource` Postgres table entirely — that
table only exists for per-user UI bookkeeping, not needed for a fixed eval world.

One collection PER CITY (`eval_venues_<city>`), not one shared collection for
all cities — this mirrors how production actually indexes data (per user/city/
event, via indexing_service.get_city_collection_name), and matters concretely:
`rag_service.query_with_smart_filters` filters by capacity/budget but has no
city filter, so a single mixed-city collection lets an unrelated city's venue
leak into "Manchester" results purely because it also satisfies the capacity
filter — this was caught by an early eval run (a query for "Manchester, 450
guests" pulled in Edinburgh's Grand Ballroom, tanking context_precision for a
reason that had nothing to do with the pipeline's actual quality).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from app.services import rag_service, venue_service

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "venues.json"

# Collection covering every city — used for "which venue overall" queries that
# deliberately span the whole fixture.
EVAL_COLLECTION_NAME = "eval_venues_all"


def city_collection_name(city: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", city.lower().strip()).strip("_")
    return f"eval_venues_{slug}"


def load_fixture() -> List[Dict]:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def all_city_collections() -> List[str]:
    venues = load_fixture()
    cities = sorted({v.get("city", "") for v in venues if v.get("city")})
    return [city_collection_name(c) for c in cities]


def _build_chunks(venues: List[Dict], collection_name: str) -> Tuple[List[str], List[dict], List[str]]:
    chunks: List[str] = []
    metadatas: List[dict] = []
    source_name = "Golden Dataset Fixture"

    for i, v in enumerate(venues):
        city = v.get("city", "")

        # Chunk 1: rich text for semantic search — same renderer real indexing uses
        rich_text = venue_service.venue_to_text(v, city)
        chunks.append(rich_text)
        metadatas.append({
            "source": source_name,
            "event_type": "eval",
            "city": city,
            "chunk_type": "venue",
            "chunk_index": i,
            "venue_name": v.get("name", ""),
            "venue_type": v.get("type", ""),
            "api_source": v.get("source", ""),
            "capacity": str(v.get("capacity", "")),
            "phone": str(v.get("phone", "")),
            "website": str(v.get("website", "")),
            "has_canvas_data": "False",
            "canvas_from_price": "",
            "lat": str(v.get("lat", "")),
            "lon": str(v.get("lon", "")),
        })

        # Chunk 2: raw JSON — exact data for precise lookups
        raw_text = f"RAW JSON for {v.get('name', 'venue')} ({city}):\n" + json.dumps(v, ensure_ascii=False)
        chunks.append(raw_text)
        metadatas.append({
            "source": source_name,
            "event_type": "eval",
            "city": city,
            "chunk_type": "raw_json",
            "chunk_index": i,
            "venue_name": v.get("name", ""),
            "api_source": v.get("source", ""),
        })

    ids = [f"{collection_name}_{i}" for i in range(len(chunks))]
    return chunks, metadatas, ids


def ensure_fixture_indexed(force: bool = True) -> int:
    """
    Index the fixture into one collection per city PLUS one collection
    covering all venues (EVAL_COLLECTION_NAME). If force=True (default),
    deletes each existing eval collection first so the indexed world always
    exactly matches the current fixture file. If force=False, skips
    re-indexing a collection when it already has the expected chunk count.

    Returns the total number of chunks indexed (or already present, if skipped).
    """
    venues = load_fixture()
    by_city: Dict[str, List[Dict]] = {}
    for v in venues:
        by_city.setdefault(v.get("city", ""), []).append(v)

    groups: List[Tuple[str, List[Dict]]] = [(city_collection_name(c), vs) for c, vs in by_city.items()]
    groups.append((EVAL_COLLECTION_NAME, venues))

    total = 0
    for collection_name, group_venues in groups:
        expected_chunks = len(group_venues) * 2

        if not force:
            docs, _ = rag_service.get_chunks_by_filter(
                collection_name, where={"chunk_type": "venue"}, include=["documents"]
            )
            if len(docs) == len(group_venues):
                total += expected_chunks
                continue

        rag_service.delete_collection(collection_name)
        chunks, metadatas, ids = _build_chunks(group_venues, collection_name)
        total += rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

    return total
