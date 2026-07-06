"""
Event Planning Agent — adapted from modules/agent.py for the backend service layer.
Uses the same tool-calling loop but imports from backend services instead of modules/.
"""

import json
import re
from typing import Dict, Generator, List, Optional

from app.core.config import get_settings

settings = get_settings()

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "Search the indexed knowledge base — contains the event brief document "
                "AND venue data (name, type, address, capacity, contact). "
                "Use this first for ANY question about the event or venues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The specific question or topic to search for."},
                    "n_results": {"type": "integer", "description": "Number of chunks to retrieve (default 8, max 15).", "default": 8},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_by_capacity",
            "description": (
                "Scan the indexed venues and return ONLY those whose confirmed capacity "
                "is >= the requested minimum."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "min_capacity": {"type": "integer", "description": "Minimum number of people the venue must accommodate."},
                },
                "required": ["min_capacity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_venues_live",
            "description": (
                "Search for venues in real-time from Geoapify / OpenStreetMap APIs. "
                "Use ONLY when the indexed data has too few results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name to search in."},
                    "categories": {"type": "array", "items": {"type": "string"}, "description": "Venue categories."},
                    "radius_km": {"type": "integer", "description": "Search radius in kilometres (default 5).", "default": 5},
                    "min_capacity": {"type": "integer", "description": "Optional minimum capacity filter."},
                },
                "required": ["city", "categories"],
            },
        },
    },
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

_SYSTEM_PROMPT = """You are an expert event planning assistant with access to four tools.

DECISION RULES:
1. ALWAYS call rag_search first — it checks the indexed event brief and venue database.
2. If the user mentions a number of guests / capacity → ALSO call filter_by_capacity.
3. If the user asks about food, catering, restaurants, or dining → call find_catering_options.
4. If rag_search returns fewer than 3 relevant venue results → consider search_venues_live.
5. You may call multiple tools before answering. Think step by step.
6. Never make up venue names, phone numbers, capacities or prices.

FINAL ANSWER FORMAT:
When you have enough information, output ONLY a JSON object with these exact fields:
{
  "answer": "<your complete markdown answer>",
  "sources_used": ["<venue or doc name>", ...],
  "confidence": "high" | "medium" | "low",
  "query_interpretation": "<one sentence: how you understood the question>",
  "tools_used": ["<tool names you called>"]
}
"""


def _tool_rag_search(collection_name: str, query: str, n_results: int = 8) -> Dict:
    from app.services.rag_service import query_collection
    try:
        docs, metas = query_collection(collection_name, query, n_results=n_results)
    except Exception as exc:
        return {"error": str(exc), "chunks_found": 0, "results": []}
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
    return {"chunks_found": len(results), "collection": collection_name, "results": results}


def _tool_filter_by_capacity(collection_name: str, min_capacity: int) -> Dict:
    from app.services.rag_service import _chroma_client
    try:
        client = _chroma_client()
        col = client.get_collection(collection_name)
        raw = col.get(where={"chunk_type": "venue"}, include=["documents", "metadatas"])
    except Exception as exc:
        return {"error": str(exc), "matched_count": 0, "venues": []}

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
    matched.sort(key=lambda v: _parse_cap(v["capacity"]), reverse=True)
    return {"min_capacity_requested": min_capacity, "matched_count": len(matched), "venues": matched[:20]}


def _tool_search_venues_live(
    city: str,
    categories: List[str],
    radius_km: int = 5,
    min_capacity: Optional[int] = None,
) -> Dict:
    from app.services.venue_service import fetch_all_city_venues, get_city_coords
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
        def _cap(v: Dict) -> int:
            nums = re.findall(r"\d+", str(v.get("capacity_raw", "") or ""))
            return int(nums[0]) if nums else 0
        venues = [v for v in venues if _cap(v) >= min_capacity or not v.get("capacity")]

    summaries = []
    for v in venues[:20]:
        summaries.append({
            "name": v["name"],
            "type": v.get("type", ""),
            "address": v.get("address", ""),
            "capacity": v.get("capacity", ""),
            "phone": v.get("phone", ""),
            "email": v.get("email", ""),
            "website": v.get("website", ""),
            "source": v.get("source", ""),
        })
    return {"city": city, "venues_found": len(venues), "source_counts": counts, "venues": summaries}


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
    "rag_search": _tool_rag_search,
    "filter_by_capacity": _tool_filter_by_capacity,
    "search_venues_live": _tool_search_venues_live,
    "find_catering_options": _tool_find_catering_options,
}


def run_agent(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, None]:
    """
    Run the event planning agent. Yields trace events.
    Final event has type='answer' with the structured response.
    """
    from app.services.rag_service import _openai_client

    client = _openai_client()
    chat_history = chat_history or []
    MAX_ITERATIONS = 6

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(chat_history[-6:])
    messages.append({"role": "user", "content": query})

    tools_called: List[str] = []
    yield {"type": "thinking", "message": "Agent reasoning — choosing first tool…"}

    for iteration in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2500,
        )

        choice = response.choices[0]
        messages.append(choice.message)

        if choice.finish_reason == "tool_calls":
            for tc in choice.message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                if tool_name == "rag_search":
                    tool_args.setdefault("n_results", 8)
                    call_args = {"collection_name": collection_name, **tool_args}
                elif tool_name == "filter_by_capacity":
                    call_args = {"collection_name": collection_name, **tool_args}
                elif tool_name == "search_venues_live":
                    if city and "city" not in tool_args:
                        tool_args["city"] = city
                    call_args = tool_args
                elif tool_name == "find_catering_options":
                    call_args = {"collection_name": collection_name, **tool_args}
                    if city and "city" not in call_args:
                        call_args["city"] = city
                else:
                    call_args = tool_args

                yield {"type": "tool_call", "tool": tool_name, "args": tool_args}

                tool_fn = TOOL_REGISTRY.get(tool_name)
                if tool_fn:
                    try:
                        result = tool_fn(**call_args)
                    except Exception as exc:
                        result = {"error": str(exc)}
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}

                tools_called.append(tool_name)

                count = (
                    result.get("chunks_found") or
                    result.get("matched_count") or
                    result.get("venues_found") or 0
                )
                yield {
                    "type": "tool_result",
                    "tool": tool_name,
                    "summary": _summarise_result(tool_name, result),
                    "count": count,
                    "result": result,
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

            yield {"type": "thinking", "message": f"Iteration {iteration + 1}: analysing tool results…"}

        elif choice.finish_reason in ("stop", "length"):
            raw_content = choice.message.content or ""
            try:
                cleaned = raw_content.strip()
                if cleaned.startswith("```"):
                    cleaned = "\n".join(cleaned.split("\n")[1:])
                if cleaned.endswith("```"):
                    cleaned = "\n".join(cleaned.split("\n")[:-1])
                answer_data = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                answer_data = {
                    "answer": raw_content or "I was unable to generate a structured answer.",
                    "sources_used": [],
                    "confidence": "low",
                    "query_interpretation": query,
                    "tools_used": tools_called,
                }
            answer_data.setdefault("tools_used", tools_called)
            answer_data["tools_used"] = list(set(answer_data["tools_used"] + tools_called))
            yield {"type": "answer", "data": answer_data}
            return
        else:
            yield {"type": "error", "message": f"Unexpected finish_reason: {choice.finish_reason}"}
            return

    yield {
        "type": "answer",
        "data": {
            "answer": "I reached the maximum number of reasoning steps. Please try rephrasing your question.",
            "sources_used": [],
            "confidence": "low",
            "query_interpretation": query,
            "tools_used": tools_called,
        },
    }


def _summarise_result(tool_name: str, result: Dict) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    if tool_name == "rag_search":
        n = result.get("chunks_found", 0)
        return f"{n} chunks found"
    if tool_name == "filter_by_capacity":
        n = result.get("matched_count", 0)
        mn = result.get("min_capacity_requested", "?")
        return f"{n} venues with capacity >= {mn}"
    if tool_name == "search_venues_live":
        n = result.get("venues_found", 0)
        city = result.get("city", "")
        return f"{n} venues found live in {city}"
    return str(result)[:200]
