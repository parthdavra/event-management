"""
Venue Specialist Agent — finds and filters venues, and answers general
questions about the indexed event brief (rag_search covers both).
"""

import re
from typing import Dict, Generator, List, Optional

from langfuse import observe

from app.core.config import get_settings
from app.prompts.venue_agent_system import PROMPT as _SYSTEM_PROMPT
from app.services.agents._tool_loop import run_tool_loop

settings = get_settings()

AGENT_NAME = "venue_agent"

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
]

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
    from app.services.rag_service import get_chunks_by_filter
    try:
        docs, metas = get_chunks_by_filter(
            collection_name, where={"chunk_type": "venue"}, include=["documents", "metadatas"]
        )
    except Exception as exc:
        return {"error": str(exc), "matched_count": 0, "venues": []}

    def _parse_cap(cap_str: str) -> int:
        nums = re.findall(r"\d+", cap_str or "")
        return int(nums[0]) if nums else 0

    matched = []
    for doc, meta in zip(docs, metas):
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

    venues, counts, _ = fetch_all_city_venues(
        city=city,
        categories=categories,
        radius_km=radius_km,
        use_foursquare=True,
        use_geoapify=True,
        enrich_details=True,
        max_venues=100,
        coords=coords,
    )

    # Supplement with the shared MCP server's venue search (additional source, not a
    # replacement — MCP lacks the Canvas enrichment the local fetch above provides).
    try:
        from app.services.mcp_client import MCPToolsClient
        mcp_result = MCPToolsClient().call_tool_sync("em_search_venues", {
            "city": city,
            "categories": categories,
            "min_capacity": min_capacity or 0,
            "radius_km": radius_km,
            "caller_id": settings.mcp_caller_id,
        })
        mcp_venues = (mcp_result or {}).get("venues", [])
    except Exception:
        mcp_venues = []

    if mcp_venues:
        seen = {re.sub(r"[^a-z0-9]", "", v["name"].lower()) for v in venues if v.get("name")}
        for v in mcp_venues:
            key = re.sub(r"[^a-z0-9]", "", (v.get("name") or "").lower())
            if key and key not in seen:
                seen.add(key)
                venues.append(v)
        counts["MCP"] = len(mcp_venues)

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


TOOL_REGISTRY = {
    "rag_search": _tool_rag_search,
    "filter_by_capacity": _tool_filter_by_capacity,
    "search_venues_live": _tool_search_venues_live,
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


@observe(as_type="agent", name="venue_agent")
def run(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, Dict]:
    def _build_call_args(tool_name: str, tool_args: Dict) -> Dict:
        if tool_name == "rag_search":
            tool_args.setdefault("n_results", 8)
            return {"collection_name": collection_name, **tool_args}
        if tool_name == "filter_by_capacity":
            return {"collection_name": collection_name, **tool_args}
        if tool_name == "search_venues_live":
            if city and "city" not in tool_args:
                tool_args["city"] = city
            return tool_args
        return tool_args

    return (yield from run_tool_loop(
        agent_name=AGENT_NAME,
        system_prompt=_SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        tool_registry=TOOL_REGISTRY,
        build_call_args=_build_call_args,
        query=query,
        chat_history=chat_history,
        max_iterations=5,
        summarise_result=_summarise_result,
    ))
