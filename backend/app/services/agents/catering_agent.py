"""
Catering Specialist Agent — finds food and catering options for a given
venue/event.
"""

import re
from typing import Dict, Generator, List, Optional

from app.core.config import get_settings
from app.services.agents._tool_loop import run_tool_loop

settings = get_settings()

AGENT_NAME = "catering_agent"

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "find_catering_options",
            "description": (
                "Find food and catering options for an event at a specific venue. "
                "Checks whether the venue has in-house catering, then searches for external options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_name": {"type": "string", "description": "Exact name of the event venue."},
                    "event_type": {"type": "string", "description": "Type of event (corporate, birthday, wedding, etc.)."},
                    "guest_count": {"type": "integer", "description": "Number of guests.", "default": 50},
                    "prefer_external": {"type": "boolean", "description": "True if user wants external catering.", "default": False},
                    "city": {"type": "string", "description": "City where the venue is located."},
                    "initial_radius_km": {"type": "integer", "description": "Starting search radius in km.", "default": 1},
                },
                "required": ["venue_name", "event_type"],
            },
        },
    },
]

_SYSTEM_PROMPT = """You are the Catering Specialist Agent, an expert in food and catering options for events.

DECISION RULES:
1. Call find_catering_options for any question about food, catering, restaurants, or dining at a venue.
2. Never make up caterer names, phone numbers, or prices.
3. You are being consulted by a Lead Orchestrator agent, not the end user directly — answer the
   question you were asked as precisely and completely as possible, including the venue name and
   event details it gave you.

FINAL ANSWER FORMAT:
When you have enough information, output ONLY a JSON object with these exact fields:
{
  "answer": "<your complete markdown answer>",
  "sources_used": ["<caterer or venue name>", ...],
  "confidence": "high" | "medium" | "low",
  "query_interpretation": "<one sentence: how you understood the question>",
  "tools_used": ["<tool names you called>"]
}
"""


def _tool_find_catering_options(
    collection_name: str,
    venue_name: str,
    event_type: str = "corporate",
    guest_count: int = 50,
    prefer_external: bool = False,
    city: str = "",
    initial_radius_km: int = 1,
) -> Dict:
    from app.services.venue_service import (
        get_catering_profile,
        haversine_km,
        _IN_HOUSE_CATERING_TYPES,
    )
    from app.services.rag_service import _chroma_client
    import requests as _req

    venue_lat = venue_lon = None
    venue_type = ""
    has_cuisine = False

    try:
        client = _chroma_client()
        col = client.get_collection(collection_name)
        raw = col.get(where={"chunk_type": "venue"}, include=["documents", "metadatas"])
        for doc, meta in zip(raw.get("documents", []), raw.get("metadatas", [])):
            if venue_name.lower() in meta.get("venue_name", "").lower():
                venue_type = meta.get("venue_type", "").lower()
                try:
                    venue_lat = float(meta.get("lat", "") or "")
                    venue_lon = float(meta.get("lon", "") or "")
                except (ValueError, TypeError):
                    pass
                if not venue_lat:
                    m = re.search(r"Coordinates:\s*([\d.-]+),\s*([\d.-]+)", doc)
                    if m:
                        venue_lat, venue_lon = float(m.group(1)), float(m.group(2))
                has_cuisine = bool(
                    re.search(r"Cuisine:\s*\S", doc) or
                    re.search(r"Catering:", doc, re.IGNORECASE)
                )
                break
    except Exception:
        pass

    if not venue_lat and settings.geoapify_api_key:
        try:
            search_text = f"{venue_name}, {city}" if city else venue_name
            r = _req.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": search_text, "limit": 1, "apiKey": settings.geoapify_api_key},
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

    in_house = any(t in venue_type for t in _IN_HOUSE_CATERING_TYPES) or has_cuisine
    profile = get_catering_profile(event_type)

    # Fetch external catering options
    geo_cats = profile.get("geoapify_cats", "catering.restaurant,catering.cafe")
    osm_amenity = profile.get("osm_amenity", ["restaurant", "cafe"])
    radius_m = initial_radius_km * 1000
    max_radius_m = 5000
    external: List[Dict] = []
    final_radius_m = radius_m

    import requests as _req2
    while radius_m <= max_radius_m:
        candidates: List[Dict] = []
        if settings.geoapify_api_key:
            try:
                resp = _req2.get(
                    "https://api.geoapify.com/v2/places",
                    params={
                        "filter": f"circle:{venue_lon},{venue_lat},{radius_m}",
                        "categories": geo_cats,
                        "limit": 50,
                        "apiKey": settings.geoapify_api_key,
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    for feat in resp.json().get("features", []):
                        p = feat.get("properties", {})
                        name = p.get("name", "").strip()
                        if not name:
                            continue
                        raw = p.get("datasource", {}).get("raw", {})
                        contact = p.get("contact", {})
                        vlat, vlon = p.get("lat"), p.get("lon")
                        dist_km = haversine_km(venue_lat, venue_lon, vlat, vlon) if vlat and vlon else None
                        candidates.append({
                            "name": name,
                            "type": (p.get("categories", [""])[0].split(".")[-1].replace("_", " ").title()),
                            "address": p.get("formatted", ""),
                            "phone": contact.get("phone") or raw.get("phone", ""),
                            "email": contact.get("email") or raw.get("email", ""),
                            "website": raw.get("website", ""),
                            "cuisine": raw.get("cuisine", "").replace(";", ", "),
                            "lat": vlat,
                            "lon": vlon,
                            "distance_km": round(dist_km, 2) if dist_km is not None else None,
                            "distance_label": (
                                "< 2 min walk" if dist_km and dist_km / 5.0 * 60 < 2
                                else f"~{int(round(dist_km / 5.0 * 60))} min walk" if dist_km else "unknown"
                            ),
                            "source": "Geoapify",
                        })
            except Exception:
                pass

        seen: set = set()
        unique: List[Dict] = []
        for c in candidates:
            key = re.sub(r"[^a-z0-9]", "", c["name"].lower())
            if key and key not in seen:
                seen.add(key)
                unique.append(c)
        unique.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 99)
        external = unique
        final_radius_m = radius_m
        if len(external) >= 3:
            break
        radius_m += 1000

    return {
        "venue_name": venue_name,
        "event_type": event_type,
        "guest_count": guest_count,
        "in_house_catering": in_house,
        "in_house_note": (
            "This venue appears to have its own catering / food service."
            if in_house else
            "This venue does not appear to have in-house catering."
        ),
        "profile": {"label": profile["label"], "food_style": profile["food_style"], "notes": profile["notes"]},
        "search_radius_km": round(final_radius_m / 1000, 1),
        "external_options_found": len(external),
        "external_options": external[:15],
        "radius_expanded": final_radius_m > initial_radius_km * 1000,
    }


TOOL_REGISTRY = {
    "find_catering_options": _tool_find_catering_options,
}


def _summarise_result(tool_name: str, result: Dict) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    if tool_name == "find_catering_options":
        n = result.get("external_options_found", 0)
        in_house = result.get("in_house_catering")
        return f"{n} external options found" + (" · has in-house catering" if in_house else "")
    return str(result)[:200]


def run(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, Dict]:
    def _build_call_args(tool_name: str, tool_args: Dict) -> Dict:
        call_args = {"collection_name": collection_name, **tool_args}
        if city and "city" not in call_args:
            call_args["city"] = city
        return call_args

    return (yield from run_tool_loop(
        agent_name=AGENT_NAME,
        system_prompt=_SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        tool_registry=TOOL_REGISTRY,
        build_call_args=_build_call_args,
        query=query,
        chat_history=chat_history,
        max_iterations=3,
        summarise_result=_summarise_result,
    ))
