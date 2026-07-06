"""
MCP-Tools proxy endpoints.

All routes forward requests to the MCP-Tools server (FastMCP SSE) and return
the results as standard JSON.  Auth is required — the caller's JWT must be valid.

Endpoints:
    GET  /mcp/tools                     — list all available MCP tools
    GET  /mcp/prompts                   — list all available MCP prompt templates
    POST /mcp/call                      — call any tool by name with arbitrary args
    POST /mcp/prompt                    — fetch a rendered prompt template
    POST /mcp/venues/search             — em_search_venues (typed shortcut)
    POST /mcp/budget                    — em_budget_planner (typed shortcut)
    GET  /mcp/catering/{event_type}     — em_catering_guide (typed shortcut)
    GET  /mcp/geocode/{city}            — em_geocode_city (typed shortcut)
    GET  /mcp/prompts/planning-checklist — em_event_planning_checklist
    GET  /mcp/prompts/venue-rfp         — em_venue_rfp_template
    GET  /mcp/prompts/budget-guide      — em_budget_breakdown_guide
    GET  /mcp/prompts/catering-brief    — em_catering_brief_template
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, HTTPException

from app.api.deps import get_current_user
from app.schemas.mcp import (
    MCPToolRequest,
    MCPToolResponse,
    MCPPromptRequest,
    MCPPromptResponse,
    VenueSearchMCPRequest,
    BudgetPlannerMCPRequest,
)
from app.services.mcp_client import MCPToolsClient, get_mcp_client

router = APIRouter(prefix="/mcp", tags=["MCP Tools"])


def _mcp(_: object = Depends(get_current_user)) -> MCPToolsClient:
    """Dependency: MCP client using the project caller ID from settings."""
    return get_mcp_client()


# ── Discovery ─────────────────────────────────────────────────────────────────

@router.get("/tools")
async def list_tools(mcp: MCPToolsClient = Depends(_mcp)) -> list[dict]:
    """List all tools registered on the MCP-Tools server."""
    try:
        return await mcp.list_tools()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP server unreachable: {exc}")


@router.get("/prompts")
async def list_prompts(mcp: MCPToolsClient = Depends(_mcp)) -> list[dict]:
    """List all prompt templates registered on the MCP-Tools server."""
    try:
        return await mcp.list_prompts()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP server unreachable: {exc}")


# ── Generic call ──────────────────────────────────────────────────────────────

@router.post("/call", response_model=MCPToolResponse)
async def call_tool(
    body: MCPToolRequest,
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPToolResponse:
    """Call any MCP tool by name with arbitrary arguments."""
    try:
        result = await mcp.call_tool(body.tool_name, body.arguments)
        return MCPToolResponse(tool_name=body.tool_name, result=result, success=True)
    except Exception as exc:
        return MCPToolResponse(
            tool_name=body.tool_name,
            result=None,
            success=False,
            error=str(exc),
        )


@router.post("/prompt", response_model=MCPPromptResponse)
async def get_prompt(
    body: MCPPromptRequest,
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPPromptResponse:
    """Fetch and render any MCP prompt template."""
    try:
        text = await mcp.get_prompt(body.prompt_name, body.arguments)
        return MCPPromptResponse(prompt_name=body.prompt_name, text=text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Typed event-management shortcuts ─────────────────────────────────────────

@router.post("/venues/search")
async def mcp_search_venues(
    body: VenueSearchMCPRequest,
    mcp: MCPToolsClient = Depends(_mcp),
) -> Any:
    """Search for event venues via MCP em_search_venues tool."""
    try:
        return await mcp.search_venues(
            city=body.city,
            categories=body.categories,
            min_capacity=body.min_capacity,
            radius_km=body.radius_km,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/budget")
async def mcp_budget_planner(
    body: BudgetPlannerMCPRequest,
    mcp: MCPToolsClient = Depends(_mcp),
) -> Any:
    """Get budget split via MCP em_budget_planner tool."""
    try:
        return await mcp.budget_planner(
            event_type=body.event_type,
            total_budget=body.total_budget,
            currency=body.currency,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/catering/{event_type}")
async def mcp_catering_guide(
    event_type: str,
    mcp: MCPToolsClient = Depends(_mcp),
) -> Any:
    """Get catering guide via MCP em_catering_guide tool."""
    try:
        return await mcp.catering_guide(event_type)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/geocode/{city}")
async def mcp_geocode_city(
    city: str,
    mcp: MCPToolsClient = Depends(_mcp),
) -> Any:
    """Geocode a city via MCP em_geocode_city tool."""
    try:
        return await mcp.geocode_city(city)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ── Typed prompt shortcuts ────────────────────────────────────────────────────

@router.get("/prompts/planning-checklist")
async def mcp_prompt_planning_checklist(
    event_type: str = Query(default="corporate"),
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPPromptResponse:
    """Get an event planning checklist prompt."""
    try:
        text = await mcp.prompt_planning_checklist(event_type)
        return MCPPromptResponse(prompt_name="em_event_planning_checklist", text=text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/prompts/venue-rfp")
async def mcp_prompt_venue_rfp(
    event_type: str = Query(default="corporate"),
    guest_count: int = Query(default=100),
    city: str = Query(default="London"),
    event_date: str = Query(default="TBC"),
    budget: str = Query(default="TBC"),
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPPromptResponse:
    """Get a venue RFP template prompt."""
    try:
        text = await mcp.prompt_venue_rfp(event_type, guest_count, city, event_date, budget)
        return MCPPromptResponse(prompt_name="em_venue_rfp_template", text=text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/prompts/budget-guide")
async def mcp_prompt_budget_guide(
    event_type: str = Query(default="corporate"),
    total_budget: str = Query(default="£10,000"),
    guest_count: int = Query(default=100),
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPPromptResponse:
    """Get a budget breakdown guide prompt."""
    try:
        text = await mcp.prompt_budget_guide(event_type, total_budget, guest_count)
        return MCPPromptResponse(prompt_name="em_budget_breakdown_guide", text=text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/prompts/catering-brief")
async def mcp_prompt_catering_brief(
    event_type: str = Query(default="corporate"),
    guest_count: int = Query(default=100),
    dietary_notes: str = Query(default=""),
    mcp: MCPToolsClient = Depends(_mcp),
) -> MCPPromptResponse:
    """Get a catering brief template prompt."""
    try:
        text = await mcp.prompt_catering_brief(event_type, guest_count, dietary_notes)
        return MCPPromptResponse(prompt_name="em_catering_brief_template", text=text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
