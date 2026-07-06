from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator


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


class VenueOut(BaseModel):
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
