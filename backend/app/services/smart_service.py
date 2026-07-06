"""
Smart service layer: tries MCP-Tools first, falls back to local implementations.

Every public function returns the result dict with two extra keys:
  tool_source: "mcp" | "local"
  tool_name:   the specific tool / function that produced the result

Internal helper
---------------
_try_mcp(coro, tool_name) → (result, ok: bool)
  Runs the MCP coroutine with a shared timeout.  Returns (result, True) on
  success or (None, False) on any failure — callers decide the fallback.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from app.schemas.venue import VenueSearchRequest
from app.services import canvas_service
from app.services.mcp_client import MCPToolsClient
from app.services.venue_service import (
    _map_thumbnail_url,
    fetch_venues,
    get_catering_guide,
)

logger = logging.getLogger(__name__)

_MCP_TIMEOUT = 25.0  # seconds before giving up on the MCP server


# ── Local fallback data ───────────────────────────────────────────────────────

_LOCAL_BUDGET_SPLITS: dict[str, list[tuple[str, float]]] = {
    "corporate":      [("Venue Hire", .35), ("Catering & Food", .40), ("AV & Equipment", .10), ("Decor & Branding", .08), ("Contingency", .07)],
    "conference":     [("Venue Hire", .38), ("Catering", .30), ("AV & Tech", .15), ("Speaker / Facilitation", .10), ("Contingency", .07)],
    "networking":     [("Venue Hire", .40), ("Catering & Drinks", .38), ("Decor & Signage", .10), ("Photography", .05), ("Contingency", .07)],
    "wedding":        [("Venue Hire", .30), ("Catering & Bar", .35), ("Flowers & Decor", .12), ("Photography & Video", .10), ("Music & Entertainment", .07), ("Contingency", .06)],
    "gala":           [("Venue Hire", .30), ("Catering & Bar", .35), ("Entertainment", .12), ("Decor & Lighting", .12), ("Contingency", .11)],
    "birthday":       [("Venue Hire", .25), ("Catering & Cake", .35), ("Entertainment", .15), ("Decor", .15), ("Contingency", .10)],
    "graduation":     [("Venue Hire", .28), ("Catering & Drinks", .38), ("Photography", .12), ("Decor", .12), ("Contingency", .10)],
    "exhibition":     [("Venue Hire", .35), ("Stand Build & Decor", .28), ("AV & Displays", .18), ("Catering", .12), ("Contingency", .07)],
    "product_launch": [("Venue Hire", .28), ("AV & Production", .22), ("Catering & Drinks", .22), ("Branding & Decor", .18), ("Contingency", .10)],
}


def _budget_key(event_type: str) -> str:
    et = event_type.lower().strip()
    for key in _LOCAL_BUDGET_SPLITS:
        if key in et or et in key:
            return key
    return "corporate"


def _local_budget_planner(event_type: str, total_budget: float, currency: str) -> dict[str, Any]:
    key = _budget_key(event_type)
    splits = _LOCAL_BUDGET_SPLITS.get(key, _LOCAL_BUDGET_SPLITS["corporate"])
    breakdown = [
        {
            "category": cat,
            "percentage": f"{int(pct * 100)}%",
            "amount": round(total_budget * pct, 2),
            "currency": currency,
            "display": f"{currency} {round(total_budget * pct, 2):,.2f}",
        }
        for cat, pct in splits
    ]
    return {
        "event_type": event_type,
        "matched_profile": key,
        "total_budget": total_budget,
        "currency": currency,
        "breakdown": breakdown,
        "note": "Adjust percentages based on local market rates and priorities.",
        "tool_source": "local",
        "tool_name": "local_budget_planner",
    }


# ── Core MCP runner ───────────────────────────────────────────────────────────

async def _try_mcp(
    coro: Coroutine,
    tool_name: str,
) -> tuple[Any, bool]:
    """
    Run any MCP tool coroutine with a shared timeout.

    Returns
    -------
    (result, True)   — MCP call succeeded; result is the parsed dict/value
    (None,  False)   — timed out, connection error, or tool returned an error
                       dict; caller should use local fallback
    """
    try:
        result = await asyncio.wait_for(coro, timeout=_MCP_TIMEOUT)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(result["error"])
        logger.info("mcp_tool_success tool=%s", tool_name)
        return result, True
    except Exception as exc:
        logger.warning(
            "mcp_tool_failed tool=%s reason=%s — falling back to local",
            tool_name, exc,
        )
        return None, False


# ── Smart venue search ────────────────────────────────────────────────────────

async def smart_search_venues(body: VenueSearchRequest) -> dict[str, Any]:
    """
    Venue search with MCP → local fallback.

    For London / Manchester, Canvas Events is the primary data source.
    MCP is skipped for these cities so Canvas Events always gets first call.
    """
    # Canvas cities bypass MCP — fetch_all_city_venues handles Canvas as primary.
    if not canvas_service.is_canvas_city(body.city):
        client = MCPToolsClient()
        result, ok = await _try_mcp(
            client.search_venues(
                city=body.city,
                categories=body.categories,
                min_capacity=body.min_capacity,
                radius_km=body.radius_km,
            ),
            "em_search_venues",
        )

        if ok:
            venues: list[dict] = result.get("venues", [])
            for v in venues:
                if not v.get("map_thumbnail_url"):
                    v["map_thumbnail_url"] = _map_thumbnail_url(v.get("lat"), v.get("lon"))
            return {
                "city": body.city,
                "venues": venues,
                "total": len(venues),
                "source_counts": result.get("source_counts", {}),
                "tool_source": "mcp",
                "tool_name": "em_search_venues",
            }

    # Local path — Canvas Events is primary for London/Manchester inside here
    resp = await asyncio.to_thread(fetch_venues, body)
    return {
        "city": resp.city,
        "venues": [v.model_dump() for v in resp.venues],
        "total": resp.total,
        "source_counts": resp.source_counts,
        "tool_source": "local",
        "tool_name": "venue_service",
        "radius_km_used": resp.radius_km_used,
    }


# ── Smart budget planner ──────────────────────────────────────────────────────

async def smart_budget_planner(
    event_type: str,
    total_budget: float,
    currency: str = "GBP",
) -> dict[str, Any]:
    """Try MCP em_budget_planner; fall back to local budget table."""
    client = MCPToolsClient()
    result, ok = await _try_mcp(
        client.budget_planner(event_type, total_budget, currency),
        "em_budget_planner",
    )

    if ok:
        result["tool_source"] = "mcp"
        result["tool_name"] = "em_budget_planner"
        return result

    return _local_budget_planner(event_type, total_budget, currency)


# ── Smart catering guide ──────────────────────────────────────────────────────

async def smart_catering_guide(event_type: str) -> dict[str, Any]:
    """Try MCP em_catering_guide; fall back to local venue_service."""
    client = MCPToolsClient()
    result, ok = await _try_mcp(
        client.catering_guide(event_type),
        "em_catering_guide",
    )

    if ok:
        profile = {k: v for k, v in result.items() if k != "event_type"}
        return {
            "event_type": event_type,
            "profile": profile,
            "tool_source": "mcp",
            "tool_name": "em_catering_guide",
        }

    result = get_catering_guide(event_type)
    result["tool_source"] = "local"
    result["tool_name"] = "catering_guide"
    return result
