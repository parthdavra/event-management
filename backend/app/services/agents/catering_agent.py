"""
Catering Specialist Agent — finds food and catering options for a given
venue/event.
"""

import re
from typing import Dict, Generator, List, Optional

from langfuse import observe

from app.core.config import get_settings
from app.prompts.catering_agent_system import PROMPT as _SYSTEM_PROMPT
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
    {
        "type": "function",
        "function": {
            "name": "get_catering_budget_estimate",
            "description": (
                "Get a recommended catering budget breakdown for an event, given the "
                "total event budget. Returns the catering share plus the full category breakdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {"type": "string", "description": "Type of event (corporate, wedding, conference, etc.)."},
                    "total_budget": {"type": "number", "description": "Total event budget as a number."},
                    "currency": {"type": "string", "description": "Currency code (default GBP).", "default": "GBP"},
                },
                "required": ["event_type", "total_budget"],
            },
        },
    },
]

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
    from app.services.rag_service import get_chunks_by_filter
    import requests as _req

    venue_lat = venue_lon = None
    venue_type = ""
    has_cuisine = False

    try:
        docs, metas = get_chunks_by_filter(
            collection_name, where={"chunk_type": "venue"}, include=["documents", "metadatas"]
        )
        for doc, meta in zip(docs, metas):
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

    # Overlay MCP's catering guide text (label/food_style/notes) onto the local profile.
    # Local geoapify_cats/osm_amenity are kept — MCP's guide doesn't return search-driving
    # fields, only descriptive text, so this is additive, not a replacement.
    try:
        from app.services.mcp_client import MCPToolsClient
        mcp_guide = MCPToolsClient().call_tool_sync("em_catering_guide", {
            "event_type": event_type,
            "caller_id": settings.mcp_caller_id,
        })
        if mcp_guide:
            profile = {
                **profile,
                "label": mcp_guide.get("label", profile.get("label")),
                "food_style": mcp_guide.get("food_style", profile.get("food_style")),
                "notes": mcp_guide.get("notes", profile.get("notes")),
            }
    except Exception:
        pass

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


def _tool_get_catering_budget_estimate(
    event_type: str,
    total_budget: float,
    currency: str = "GBP",
) -> Dict:
    from app.services.mcp_client import MCPToolsClient

    try:
        result = MCPToolsClient().call_tool_sync("em_budget_planner", {
            "event_type": event_type,
            "total_budget": total_budget,
            "currency": currency,
            "caller_id": settings.mcp_caller_id,
        })
    except Exception as exc:
        return {"error": f"Budget planner unavailable: {exc}"}

    breakdown = (result or {}).get("breakdown", [])
    if not breakdown:
        return {"error": "Budget planner returned no breakdown."}

    catering_line = next(
        (b for b in breakdown if "catering" in b.get("category", "").lower()),
        None,
    )
    return {
        "event_type": event_type,
        "total_budget": total_budget,
        "currency": currency,
        "catering_budget": catering_line,
        "full_breakdown": breakdown,
    }


TOOL_REGISTRY = {
    "find_catering_options": _tool_find_catering_options,
    "get_catering_budget_estimate": _tool_get_catering_budget_estimate,
}


def _summarise_result(tool_name: str, result: Dict) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    if tool_name == "find_catering_options":
        n = result.get("external_options_found", 0)
        in_house = result.get("in_house_catering")
        return f"{n} external options found" + (" · has in-house catering" if in_house else "")
    if tool_name == "get_catering_budget_estimate":
        line = result.get("catering_budget") or {}
        return f"Catering budget: {line.get('display', 'n/a')}"
    return str(result)[:200]


@observe(as_type="agent", name="catering_agent")
def run(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, Dict]:
    def _build_call_args(tool_name: str, tool_args: Dict) -> Dict:
        if tool_name == "get_catering_budget_estimate":
            return tool_args
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
