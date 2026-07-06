"""
Async client for the MCP-Tools server (FastMCP SSE transport).

Usage inside a FastAPI async endpoint:
    client = MCPToolsClient()
    result = await client.call_tool("em_search_venues", {"city": "London", ...})

Usage inside a sync context (e.g. background task):
    client = MCPToolsClient()
    result = client.call_tool_sync("em_budget_planner", {...})

All event-management tools require caller_id — the client injects it
automatically from MCP_CALLER_ID env var (project token).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


# ── Internal async call ───────────────────────────────────────────────────────

async def _call_tool_async(server_url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    """
    Open a single-use MCP ClientSession over SSE, call one tool, and return
    the parsed result.  Raises RuntimeError on failure.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    base = server_url.rstrip('/')
    sse_url = base if base.endswith('/sse') else f"{base}/sse"
    try:
        async with sse_client(url=sse_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

        if not result.content:
            return None

        # MCP returns a list of content blocks; first text block is the payload
        text = next(
            (block.text for block in result.content if hasattr(block, "text")),
            None,
        )
        if text is None:
            return None

        # Try to deserialise JSON; fall back to raw string
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    except Exception as exc:
        logger.error("mcp_call_failed tool=%s error=%s", tool_name, exc)
        raise RuntimeError(f"MCP tool call failed [{tool_name}]: {exc}") from exc


async def _get_prompt_async(server_url: str, prompt_name: str, arguments: dict[str, Any]) -> str:
    """Fetch an MCP prompt template and return the rendered text."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    base = server_url.rstrip('/')
    sse_url = base if base.endswith('/sse') else f"{base}/sse"
    async with sse_client(url=sse_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt(prompt_name, arguments)

    messages = result.messages or []
    parts = [
        msg.content.text
        for msg in messages
        if hasattr(msg.content, "text")
    ]
    return "\n\n".join(parts)


async def _list_tools_async(server_url: str) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    base = server_url.rstrip('/')
    sse_url = base if base.endswith('/sse') else f"{base}/sse"
    async with sse_client(url=sse_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in (result.tools or [])
    ]


async def _list_prompts_async(server_url: str) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    base = server_url.rstrip('/')
    sse_url = base if base.endswith('/sse') else f"{base}/sse"
    async with sse_client(url=sse_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()

    return [
        {
            "name": p.name,
            "description": p.description or "",
            "arguments": [
                {"name": a.name, "description": a.description, "required": a.required}
                for a in (p.arguments or [])
            ],
        }
        for p in (result.prompts or [])
    ]


# ── Public client class ───────────────────────────────────────────────────────

class MCPToolsClient:
    """
    Typed async client for all MCP-Tools server tools and prompts.

    Every tool is exposed as a typed async method so callers get IDE
    auto-complete and don't have to remember argument names.

    A generic `call_tool()` / `call_tool_sync()` pair is also provided
    for ad-hoc calls.
    """

    def __init__(
        self,
        server_url: str | None = None,
        caller_id: str | None = None,
    ) -> None:
        settings = get_settings()
        self.server_url = (server_url or settings.mcp_server_url).rstrip("/")
        self.caller_id = caller_id or settings.mcp_caller_id

    # ── Generic call helpers ──────────────────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call any MCP tool by name. Returns parsed JSON or raw string."""
        return await _call_tool_async(self.server_url, tool_name, arguments or {})

    def call_tool_sync(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Sync wrapper — safe to call from non-async contexts (background tasks)."""
        return asyncio.run(_call_tool_async(self.server_url, tool_name, arguments or {}))

    async def get_prompt(self, prompt_name: str, arguments: dict[str, Any] | None = None) -> str:
        """Fetch and render an MCP prompt template."""
        return await _get_prompt_async(self.server_url, prompt_name, arguments or {})

    async def list_tools(self) -> list[dict]:
        """Return all tools registered on the MCP server."""
        return await _list_tools_async(self.server_url)

    async def list_prompts(self) -> list[dict]:
        """Return all prompt templates registered on the MCP server."""
        return await _list_prompts_async(self.server_url)

    # ── Event Management tools (em_*) ─────────────────────────────────────────

    async def search_venues(
        self,
        city: str,
        categories: list[str] | None = None,
        min_capacity: int = 0,
        radius_km: int = 5,
    ) -> dict:
        """
        Search for event venues in a city.
        Categories: "Conference & Event Venues", "Restaurants & Cafes",
                    "Bars & Nightlife", "Hotels & Accommodation",
                    "Arts & Entertainment", "Sports & Recreation"
        """
        return await self.call_tool("em_search_venues", {
            "city": city,
            "categories": categories or ["Conference & Event Venues"],
            "min_capacity": min_capacity,
            "radius_km": radius_km,
            "caller_id": self.caller_id,
        })

    async def budget_planner(
        self,
        event_type: str,
        total_budget: float,
        currency: str = "GBP",
    ) -> dict:
        """
        Return recommended budget split for an event type.
        event_type: corporate | wedding | conference | gala | birthday |
                    graduation | networking | exhibition | product_launch
        """
        return await self.call_tool("em_budget_planner", {
            "event_type": event_type,
            "total_budget": total_budget,
            "currency": currency,
            "caller_id": self.caller_id,
        })

    async def catering_guide(self, event_type: str) -> dict:
        """Return food style, service notes, and dietary guidance for an event type."""
        return await self.call_tool("em_catering_guide", {
            "event_type": event_type,
            "caller_id": self.caller_id,
        })

    async def geocode_city(self, city: str) -> dict:
        """Resolve a city name to lat/lon coordinates."""
        return await self.call_tool("em_geocode_city", {
            "city": city,
            "caller_id": self.caller_id,
        })

    async def venue_categories(self) -> dict:
        """List all supported venue category names."""
        return await self.call_tool("em_venue_categories", {
            "caller_id": self.caller_id,
        })

    async def event_types(self) -> dict:
        """List all supported event types for budget and catering tools."""
        return await self.call_tool("em_event_types", {
            "caller_id": self.caller_id,
        })

    # ── Event Management prompts ──────────────────────────────────────────────

    async def prompt_planning_checklist(self, event_type: str = "corporate") -> str:
        """Get a comprehensive event planning checklist prompt."""
        return await self.get_prompt("em_event_planning_checklist", {
            "event_type": event_type,
        })

    async def prompt_venue_rfp(
        self,
        event_type: str = "corporate",
        guest_count: int = 100,
        city: str = "London",
        event_date: str = "TBC",
        budget: str = "TBC",
    ) -> str:
        """Get a venue Request for Proposal template prompt."""
        return await self.get_prompt("em_venue_rfp_template", {
            "event_type": event_type,
            "guest_count": str(guest_count),
            "city": city,
            "event_date": event_date,
            "budget": budget,
        })

    async def prompt_budget_guide(
        self,
        event_type: str = "corporate",
        total_budget: str = "£10,000",
        guest_count: int = 100,
    ) -> str:
        """Get a detailed budget breakdown guide prompt."""
        return await self.get_prompt("em_budget_breakdown_guide", {
            "event_type": event_type,
            "total_budget": total_budget,
            "guest_count": str(guest_count),
        })

    async def prompt_catering_brief(
        self,
        event_type: str = "corporate",
        guest_count: int = 100,
        dietary_notes: str = "",
    ) -> str:
        """Get a catering brief template prompt."""
        return await self.get_prompt("em_catering_brief_template", {
            "event_type": event_type,
            "guest_count": str(guest_count),
            "dietary_notes": dietary_notes,
        })

    # ── Calculator tools ──────────────────────────────────────────────────────

    async def add(self, a: float, b: float) -> float:
        return await self.call_tool("add", {"a": a, "b": b})

    async def subtract(self, a: float, b: float) -> float:
        return await self.call_tool("subtract", {"a": a, "b": b})

    async def multiply(self, a: float, b: float) -> float:
        return await self.call_tool("multiply", {"a": a, "b": b})

    async def divide(self, a: float, b: float) -> float:
        return await self.call_tool("divide", {"a": a, "b": b})

    async def sqrt(self, n: float) -> float:
        return await self.call_tool("sqrt", {"n": n})


# ── Module-level singleton factory ────────────────────────────────────────────

def get_mcp_client(caller_id: str | None = None) -> MCPToolsClient:
    """
    FastAPI dependency — returns a configured MCPToolsClient.

    Usage in a route:
        from app.services.mcp_client import get_mcp_client

        @router.post("/my-endpoint")
        async def my_route(mcp: MCPToolsClient = Depends(get_mcp_client)):
            venues = await mcp.search_venues("London")
    """
    return MCPToolsClient(caller_id=caller_id)
