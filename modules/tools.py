"""
Tool functions for the Event Planning Agent.

Tools are deterministic, stateless functions — no LLM, no decision-making.
The agent (in agent.py) calls these and decides what to do with the results.

Each tool returns a plain dict so it can be serialised to JSON and sent
back to the LLM as a tool-call result.
"""

import re
from typing import Dict, List, Optional


# ── Tool 1: RAG retrieval ─────────────────────────────────────────────────────

def rag_search(collection_name: str, query: str, n_results: int = 8) -> Dict:
    """
    Vector-search the indexed knowledge base (event brief + venue chunks).
    Returns the most semantically relevant chunks for the query.
    No LLM involved — pure embedding similarity.
    """
    from modules.rag import query_collection

    try:
        docs, metas = query_collection(collection_name, query, n_results=n_results)
    except Exception as e:
        return {"error": str(e), "chunks_found": 0, "results": []}

    results = []
    for doc, meta in zip(docs, metas):
        results.append({
            "text": doc,
            "chunk_type": meta.get("chunk_type", "document"),
            "venue_name": meta.get("venue_name", ""),
            "venue_type": meta.get("venue_type", ""),
            "capacity": meta.get("capacity", ""),
            "phone": meta.get("phone", ""),
            "email": meta.get("email", ""),
            "website": meta.get("website", ""),
            "source": meta.get("api_source", meta.get("source", "")),
        })

    return {
        "chunks_found": len(results),
        "collection": collection_name,
        "results": results,
    }


# ── Tool 2: Capacity filter ───────────────────────────────────────────────────

def filter_by_capacity(collection_name: str, min_capacity: int) -> Dict:
    """
    Scan the indexed venue chunks and return only those whose confirmed OR
    estimated capacity is >= min_capacity.

    Uses metadata stored at index time (no embedding call needed).
    """
    from modules.rag import _chroma_client

    try:
        client = _chroma_client()
        col = client.get_collection(collection_name)
        # Fetch all venue chunks (metadata only for speed, then grab docs too)
        raw = col.get(
            where={"chunk_type": "venue"},
            include=["documents", "metadatas"],
        )
    except Exception as e:
        return {"error": str(e), "matched_count": 0, "venues": []}

    def _parse_cap(cap_str: str) -> int:
        nums = re.findall(r"\d+", cap_str or "")
        return int(nums[0]) if nums else 0

    matched = []
    for doc, meta in zip(raw.get("documents", []), raw.get("metadatas", [])):
        confirmed = _parse_cap(meta.get("capacity", ""))
        if confirmed >= min_capacity:
            matched.append({
                "text": doc,
                "venue_name": meta.get("venue_name", ""),
                "venue_type": meta.get("venue_type", ""),
                "capacity": meta.get("capacity", ""),
                "phone": meta.get("phone", ""),
                "email": meta.get("email", ""),
                "website": meta.get("website", ""),
                "source": meta.get("api_source", meta.get("source", "")),
            })

    # Sort descending by capacity
    matched.sort(key=lambda v: _parse_cap(v["capacity"]), reverse=True)

    return {
        "min_capacity_requested": min_capacity,
        "matched_count": len(matched),
        "venues": matched[:20],
    }


# ── Tool 3: Live venue search ─────────────────────────────────────────────────

def search_venues_live(
    city: str,
    categories: List[str],
    radius_km: int = 5,
    min_capacity: Optional[int] = None,
) -> Dict:
    """
    Search for venues in real-time from Geoapify / OSM APIs.
    Use this when the indexed data doesn't have enough results or the user
    asks for venues not yet indexed.
    Returns up to 20 enriched venue records.
    """
    from modules.api_fetcher import fetch_all_city_venues, get_city_coords, venue_to_text

    coords = get_city_coords(city)
    if not coords:
        return {"error": f"Could not geocode city: {city}", "venues_found": 0, "venues": []}

    venues, counts = fetch_all_city_venues(
        city=city,
        categories=categories,
        radius_km=radius_km,
        use_foursquare=True,
        use_geoapify=True,
        enrich_details=True,
        max_venues=100,
        coords=coords,
    )

    if min_capacity:
        def _cap(v):
            nums = re.findall(r"\d+", str(v.get("capacity_raw", "") or ""))
            return int(nums[0]) if nums else 0
        venues = [v for v in venues if _cap(v) >= min_capacity or not v.get("capacity")]

    venue_summaries = []
    for v in venues[:20]:
        venue_summaries.append({
            "name": v["name"],
            "type": v.get("type", ""),
            "address": v.get("address", ""),
            "capacity": v.get("capacity", ""),
            "phone": v.get("phone", ""),
            "email": v.get("email", ""),
            "website": v.get("website", ""),
            "stars": v.get("stars", ""),
            "rooms": v.get("rooms", ""),
            "wheelchair": v.get("wheelchair", ""),
            "source": v.get("source", ""),
        })

    return {
        "city": city,
        "venues_found": len(venues),
        "source_counts": counts,
        "venues": venue_summaries,
    }


# ── Tool 4: Catering options near a venue ────────────────────────────────────

def find_catering_options(
    collection_name: str,
    venue_name: str,
    event_type: str = "corporate",
    guest_count: int = 50,
    prefer_external: bool = False,
    city: str = "",
    initial_radius_km: int = 1,
) -> Dict:
    """
    For a named venue:
      1. Check whether the venue itself has in-house catering.
      2. Search for external food/catering options nearby.
      3. Auto-expand radius by 1 km (up to 5 km) until ≥ 3 options found.
      4. Return distance + walking time from the venue for every option.

    Works for any event type: corporate, birthday, graduation, wedding, etc.
    """
    from modules.api_fetcher import (
        fetch_catering_near_venue,
        get_catering_profile,
        _IN_HOUSE_CATERING_TYPES,
        haversine_km,
    )
    from modules.rag import _chroma_client

    # ── Step 1: look up venue in the index to get type + coordinates ──────────
    venue_lat = venue_lon = None
    venue_type = ""
    has_cuisine = False

    try:
        client = _chroma_client()
        col = client.get_collection(collection_name)

        # Search metadata for this venue name
        raw = col.get(where={"chunk_type": "venue"}, include=["documents", "metadatas"])
        for doc, meta in zip(raw.get("documents", []), raw.get("metadatas", [])):
            if venue_name.lower() in meta.get("venue_name", "").lower():
                venue_type = meta.get("venue_type", "").lower()
                # Try metadata lat/lon (stored since latest indexing)
                try:
                    venue_lat = float(meta.get("lat", "") or "")
                    venue_lon = float(meta.get("lon", "") or "")
                except (ValueError, TypeError):
                    pass
                # Fallback: parse coordinates from text chunk
                if not venue_lat:
                    m = re.search(r"Coordinates:\s*([\d.-]+),\s*([\d.-]+)", doc)
                    if m:
                        venue_lat, venue_lon = float(m.group(1)), float(m.group(2))
                # Check whether venue text mentions cuisine
                has_cuisine = bool(
                    re.search(r"Cuisine:\s*\S", doc) or
                    re.search(r"Catering:", doc, re.IGNORECASE)
                )
                break
    except Exception as e:
        pass

    # ── Step 2: geocode the venue if we still have no coordinates ────────────
    if not venue_lat:
        from modules.api_fetcher import GEOAPIFY_API_KEY
        import requests as _req
        if GEOAPIFY_API_KEY:
            try:
                search_text = f"{venue_name}, {city}" if city else venue_name
                r = _req.get(
                    "https://api.geoapify.com/v1/geocode/search",
                    params={"text": search_text, "limit": 1, "apiKey": GEOAPIFY_API_KEY},
                    timeout=8,
                )
                if r.status_code == 200:
                    feats = r.json().get("features", [])
                    if feats:
                        p = feats[0]["properties"]
                        venue_lat, venue_lon = float(p["lat"]), float(p["lon"])
            except Exception:
                pass

    if not venue_lat:
        return {
            "error": f"Could not locate coordinates for venue '{venue_name}'.",
            "in_house_catering": None,
            "external_options": [],
            "profile": get_catering_profile(event_type),
        }

    # ── Step 3: decide in-house catering status ───────────────────────────────
    in_house = any(t in venue_type for t in _IN_HOUSE_CATERING_TYPES) or has_cuisine

    # ── Step 4: search external options (always — user may want both) ─────────
    profile = get_catering_profile(event_type)
    external, final_radius_m = fetch_catering_near_venue(
        venue_lat=venue_lat,
        venue_lon=venue_lon,
        event_type=event_type,
        initial_radius_m=initial_radius_km * 1000,
        max_radius_m=5000,
    )

    # Add per-option distance from the venue (already computed in fetch fn,
    # but recalculate cleanly here for display)
    for opt in external:
        if opt.get("lat") and opt.get("lon"):
            d = haversine_km(venue_lat, venue_lon, opt["lat"], opt["lon"])
            opt["distance_km"] = round(d, 2)
            mins = d / 5.0 * 60
            opt["distance_label"] = (
                "< 2 min walk" if mins < 2 else f"~{int(round(mins))} min walk"
            )

    return {
        "venue_name": venue_name,
        "venue_lat": venue_lat,
        "venue_lon": venue_lon,
        "event_type": event_type,
        "guest_count": guest_count,
        "in_house_catering": in_house,
        "in_house_note": (
            "This venue appears to have its own catering / food service."
            if in_house else
            "This venue does not appear to have in-house catering."
        ),
        "profile": {
            "label": profile["label"],
            "food_style": profile["food_style"],
            "notes": profile["notes"],
        },
        "search_radius_km": round(final_radius_m / 1000, 1),
        "external_options_found": len(external),
        "external_options": external[:15],
        "radius_expanded": final_radius_m > initial_radius_km * 1000,
    }


# ── Tool registry (maps name → callable, used by agent) ──────────────────────

TOOL_REGISTRY = {
    "rag_search": rag_search,
    "filter_by_capacity": filter_by_capacity,
    "search_venues_live": search_venues_live,
    "find_catering_options": find_catering_options,
}
