from typing import Any
from pydantic import BaseModel


class MCPToolRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


class MCPToolResponse(BaseModel):
    tool_name: str
    result: Any
    success: bool
    error: str | None = None


class MCPPromptRequest(BaseModel):
    prompt_name: str
    arguments: dict[str, str] = {}


class MCPPromptResponse(BaseModel):
    prompt_name: str
    text: str


class VenueSearchMCPRequest(BaseModel):
    city: str
    categories: list[str] = ["Conference & Event Venues"]
    min_capacity: int = 0
    radius_km: int = 5


class BudgetPlannerMCPRequest(BaseModel):
    event_type: str
    total_budget: float
    currency: str = "GBP"
