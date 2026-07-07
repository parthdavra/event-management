from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class VenueSearchRequest(BaseModel):
    city: str
    categories: List[str]
    radius_km: int = 5
    use_foursquare: bool = True
    use_geoapify: bool = True
    enrich_details: bool = True
    max_venues: int = 300
    min_capacity: int = 0
    event_type: str = ""
    max_radius_km: int = 25
    venue_hire_budget: float = 0  # 0 = no budget filter; positive = max venue hire spend


class VenueOut(BaseModel):
    # extra='allow' so Canvas-rich fields (canvas_price_guide, canvas_features, etc.)
    # survive the response model and reach the frontend intact.
    model_config = ConfigDict(extra="allow")

    name: str
    type: Optional[str] = None
    address: Optional[str] = None
    capacity: Optional[str] = None
    capacity_raw: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    opening_hours: Optional[str] = None
    cuisine: Optional[str] = None
    wheelchair: Optional[str] = None
    internet_access: Optional[str] = None
    outdoor_seating: Optional[str] = None
    stars: Optional[str] = None
    rooms: Optional[str] = None
    description: Optional[str] = None
    operator: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    rating: Optional[float] = None
    source: Optional[str] = None
    map_thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None
    price_per_day: Optional[str] = None
    price_per_hour: Optional[str] = None
    price_range: Optional[str] = None
    min_spend: Optional[str] = None
    event_types: Optional[List[str]] = None
    event_type_match: Optional[bool] = None
    # Budget-fit fields (set by backend when venue_hire_budget is provided)
    within_hire_budget: Optional[bool] = None
    over_hire_budget: Optional[bool] = None
    parsed_price: Optional[float] = None
    # Canvas Events rich structured data
    canvas_price_guide: Optional[Dict[str, Any]] = None
    canvas_capacity_detail: Optional[Dict[str, Any]] = None
    canvas_spaces: Optional[List[Dict[str, Any]]] = None
    canvas_perfect_for: Optional[List[str]] = None
    canvas_features: Optional[Dict[str, List[str]]] = None

    @field_validator("wheelchair", "internet_access", "outdoor_seating", "stars", "rooms",
                     "capacity_raw", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v) if not isinstance(v, str) else v


class VenueSearchResponse(BaseModel):
    city: str
    venues: List[VenueOut]
    total: int
    source_counts: Dict[str, int]
    tool_source: str = "local"
    tool_name: str = "venue_service"
    radius_km_used: int = 0


class CateringGuideResponse(BaseModel):
    event_type: str
    profile: Dict[str, Any]
    tool_source: str = "local"
    tool_name: str = "catering_guide"


class BudgetPlannerRequest(BaseModel):
    event_type: str
    total_budget: float
    currency: str = "GBP"


class BudgetItem(BaseModel):
    category: str
    percentage: str
    amount: float
    currency: str
    display: str


class BudgetPlannerResponse(BaseModel):
    event_type: str
    matched_profile: str
    total_budget: float
    currency: str
    breakdown: List[BudgetItem]
    note: str = ""
    tool_source: str = "local"
    tool_name: str = "local_budget_planner"
