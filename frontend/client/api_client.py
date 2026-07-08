"""
HTTP client for all backend API calls.
Uses httpx (sync) so it works inside Streamlit's synchronous execution model.
Token is passed in at construction time (read from st.session_state by callers).
"""

from typing import Any, Dict, List, Optional

import httpx

# Default backend URL — overridden by BACKEND_URL env var when running in Docker
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Default timeouts (seconds)
DEFAULT_TIMEOUT = 60.0
LONG_TIMEOUT = 180.0  # for indexing / venue fetch which can take a while


class APIError(Exception):
    """Raised when the backend returns a non-2xx response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class EventManagementClient:
    """Synchronous HTTP client wrapping all backend endpoints."""

    def __init__(self, token: Optional[str] = None, base_url: str = BACKEND_URL):
        self.base_url = base_url.rstrip("/")
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _request(
        self,
        method: str,
        path: str,
        timeout: float = DEFAULT_TIMEOUT,
        **kwargs: Any,
    ) -> Any:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, headers=self._headers, **kwargs)
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise APIError(response.status_code, detail)
        if response.status_code == 204:
            return None
        return response.json()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def register(self, username: str, email: str, password: str) -> Dict:
        return self._request("POST", "/auth/register", json={
            "username": username, "email": email, "password": password,
        })

    def login(self, username: str, password: str) -> str:
        """Returns the JWT access token string."""
        data = self._request("POST", "/auth/login", json={
            "username": username, "password": password,
        })
        return data["access_token"]

    def me(self) -> Dict:
        return self._request("GET", "/auth/me")

    def change_password(self, old_password: str, new_password: str) -> None:
        self._request("POST", "/auth/change-password", json={
            "old_password": old_password, "new_password": new_password,
        })

    # ── Events ────────────────────────────────────────────────────────────────

    def create_event(
        self,
        title: str,
        date_time: str,  # ISO8601 string
        description: Optional[str] = None,
        location: Optional[str] = None,
        category: Optional[str] = None,
        is_shared: bool = False,
    ) -> Dict:
        return self._request("POST", "/events/", json={
            "title": title,
            "date_time": date_time,
            "description": description,
            "location": location,
            "category": category,
            "is_shared": is_shared,
        })

    def list_events(
        self,
        category: Optional[str] = None,
        sort_by: str = "date_time",
    ) -> List[Dict]:
        params: Dict[str, Any] = {"sort_by": sort_by}
        if category:
            params["category"] = category
        return self._request("GET", "/events/", params=params)

    def list_shared_events(self) -> List[Dict]:
        return self._request("GET", "/events/shared")

    def get_event(self, event_id: int) -> Dict:
        return self._request("GET", f"/events/{event_id}")

    def update_event(self, event_id: int, **fields) -> Dict:
        return self._request("PATCH", f"/events/{event_id}", json=fields)

    def delete_event(self, event_id: int) -> None:
        self._request("DELETE", f"/events/{event_id}")

    def save_event_brief(self, event_id: int, text: str) -> Dict:
        """Extract AI planning requirements from text and save to event.brief_json."""
        return self._request(
            "POST", f"/events/{event_id}/brief",
            timeout=LONG_TIMEOUT,
            json={"text": text},
        )

    def save_event_catering_brief(self, event_id: int, text: str) -> Dict:
        """Extract AI catering requirements from text and save to event.catering_json."""
        return self._request(
            "POST", f"/events/{event_id}/catering-brief",
            timeout=LONG_TIMEOUT,
            json={"text": text},
        )

    # ── Chat ─────────────────────────────────────────────────────────────────

    def get_messages(
        self, event_id: Optional[int] = None, limit: int = 100
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit}
        if event_id is not None:
            params["event_id"] = event_id
        return self._request("GET", "/chat/", params=params)

    def send_message(self, message: str, event_id: Optional[int] = None) -> Dict:
        return self._request("POST", "/chat/", json={
            "message": message,
            "event_id": event_id,
        })

    # ── Venues ────────────────────────────────────────────────────────────────

    def search_venues(
        self,
        city: str,
        categories: List[str],
        radius_km: int = 5,
        use_foursquare: bool = True,
        use_geoapify: bool = True,
        enrich_details: bool = True,
        max_venues: int = 300,
        event_type: str = "",
        max_radius_km: int = 25,
        venue_hire_budget: float = 0,
    ) -> Dict:
        return self._request(
            "POST",
            "/venues/search",
            timeout=LONG_TIMEOUT,
            json={
                "city": city,
                "categories": categories,
                "radius_km": radius_km,
                "use_foursquare": use_foursquare,
                "use_geoapify": use_geoapify,
                "enrich_details": enrich_details,
                "max_venues": max_venues,
                "event_type": event_type,
                "max_radius_km": max_radius_km,
                "venue_hire_budget": venue_hire_budget,
            },
        )

    def enrich_venue(self, venue: Dict) -> Dict:
        """Fetch Canvas Events detail page for this venue and fill empty fields."""
        return self._request("POST", "/venues/enrich", timeout=LONG_TIMEOUT, json=venue)

    def get_catering_guide(self, event_type: str) -> Dict:
        return self._request("GET", "/venues/catering-guide", params={"event_type": event_type})

    def budget_planner(self, event_type: str, total_budget: float, currency: str = "GBP") -> Dict:
        return self._request(
            "POST",
            "/venues/budget",
            json={"event_type": event_type, "total_budget": total_budget, "currency": currency},
        )

    # ── AI ────────────────────────────────────────────────────────────────────

    def ai_chat(self, query: str, chat_history: Optional[List[Dict]] = None) -> str:
        data = self._request(
            "POST",
            "/ai/chat",
            timeout=LONG_TIMEOUT,
            json={"query": query, "chat_history": chat_history or []},
        )
        return data["answer"]

    def ai_rag(
        self,
        query: str,
        collection_names: Optional[List[str]] = None,
        n_results: int = 5,
        chat_history: Optional[List[Dict]] = None,
    ) -> Dict:
        return self._request(
            "POST",
            "/ai/rag",
            timeout=LONG_TIMEOUT,
            json={
                "query": query,
                "collection_names": collection_names,
                "n_results": n_results,
                "chat_history": chat_history or [],
            },
        )

    def ai_extract_requirements(self, text: str) -> Dict:
        return self._request(
            "POST",
            "/ai/extract-requirements",
            timeout=LONG_TIMEOUT,
            json={"query": text},
        )

    def ai_get_history(self) -> List[Dict]:
        return self._request("GET", "/ai/history")

    def ai_clear_history(self) -> Dict:
        return self._request("DELETE", "/ai/history")

    def ai_metrics_summary(self) -> Dict:
        return self._request("GET", "/ai/metrics/summary")

    def ai_metrics_recent(self, limit: int = 20) -> List[Dict]:
        return self._request("GET", "/ai/metrics/recent", params={"limit": limit})

    def ai_business_summary(self) -> Dict:
        return self._request("GET", "/ai/metrics/business-summary")

    def ai_business_trend(self, days: int = 30) -> Dict:
        return self._request("GET", "/ai/metrics/business-trend", params={"days": days})

    def ai_check_input(self, query: str) -> Dict:
        return self._request(
            "POST",
            "/ai/check-input",
            timeout=30.0,
            json={"query": query},
        )

    def ai_run_agent(
        self,
        query: str,
        collection_name: str,
        city: str = "",
        chat_history: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """Run the event planning agent — returns list of trace events."""
        return self._request(
            "POST",
            "/ai/agent",
            timeout=LONG_TIMEOUT,
            json={
                "query": query,
                "collection_name": collection_name,
                "city": city,
                "chat_history": chat_history or [],
            },
        )

    # ── Indexing ──────────────────────────────────────────────────────────────

    def extract_text_from_file(self, file_bytes: bytes, filename: str) -> str:
        """Extract text from a PDF/DOCX/TXT without indexing. Returns the plain text."""
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        url = f"{self.base_url}/indexing/extract"
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        with httpx.Client(timeout=LONG_TIMEOUT) as http:
            response = http.post(url, headers=headers, files=files)
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise APIError(response.status_code, detail)
        return response.json()["text"]

    def upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        source_name: Optional[str] = None,
    ) -> Dict:
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        data = {}
        if source_name:
            data["source_name"] = source_name
        # Use a separate client without Content-Type so httpx sets multipart boundary
        url = f"{self.base_url}/indexing/upload"
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        with httpx.Client(timeout=LONG_TIMEOUT) as client:
            response = client.post(url, headers=headers, files=files, data=data)
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise APIError(response.status_code, detail)
        return response.json()

    def index_text(self, source_name: str, text: str) -> Dict:
        return self._request("POST", "/indexing/text", json={
            "source_name": source_name, "text": text,
        })

    def index_city(self, city: str, venues: List[Dict], replace_existing: bool = True) -> Dict:
        return self._request(
            "POST",
            "/indexing/city",
            timeout=LONG_TIMEOUT,
            json={"city": city, "venues": venues, "replace_existing": replace_existing},
        )

    def bulk_index_event_types(
        self,
        cities: List[str],
        event_types: List[str],
        categories: List[str],
        radius_km: int = 5,
        max_venues_per_city: int = 300,
        replace_existing: bool = True,
    ) -> Dict:
        """Start bulk indexing in a background job. Returns {job_id, status}."""
        return self._request(
            "POST",
            "/indexing/bulk-event-types",
            timeout=30.0,
            json={
                "cities": cities,
                "event_types": event_types,
                "categories": categories,
                "radius_km": radius_km,
                "max_venues_per_city": max_venues_per_city,
                "replace_existing": replace_existing,
            },
        )

    def get_bulk_index_job(self, job_id: str) -> Dict:
        """Poll for bulk-index job status. Returns {status, result, error}."""
        return self._request("GET", f"/indexing/bulk-event-types/{job_id}", timeout=15.0)

    def index_from_json(
        self,
        venues: List[Dict],
        event_type: str = "general",
        city: str = "",
        replace_existing: bool = True,
    ) -> Dict:
        """Enrich Canvas venues from their detail page then index all as chunks."""
        return self._request(
            "POST",
            "/indexing/from-json",
            timeout=LONG_TIMEOUT,
            json={"venues": venues, "event_type": event_type, "city": city, "replace_existing": replace_existing},
        )

    def index_event_type_venues(
        self,
        event_type: str,
        city: str,
        venues: List[Dict],
        replace_existing: bool = True,
    ) -> Dict:
        """Index venues into a per-event-type collection (rich text + raw JSON chunks)."""
        return self._request(
            "POST",
            "/indexing/event-type",
            timeout=LONG_TIMEOUT,
            json={"event_type": event_type, "city": city, "venues": venues, "replace_existing": replace_existing},
        )

    def index_event_plan(
        self,
        event_name: str,
        collection_slug: str,
        document_text: str,
        venues: List[Dict],
        city: str,
    ) -> Dict:
        return self._request(
            "POST",
            "/indexing/event-plan",
            timeout=LONG_TIMEOUT,
            json={
                "event_name": event_name,
                "collection_slug": collection_slug,
                "document_text": document_text,
                "venues": venues,
                "city": city,
            },
        )

    def scrape_and_index_feedr(
        self,
        city: str,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        replace_existing: bool = True,
    ) -> Dict:
        """Start Feedr.co fetch + index job via CaterDesk GraphQL API. Returns {job_id, status}."""
        payload: Dict[str, Any] = {
            "city": city,
            "replace_existing": replace_existing,
        }
        if lat is not None:
            payload["lat"] = lat
        if lon is not None:
            payload["lon"] = lon
        return self._request("POST", "/indexing/feedr", timeout=30.0, json=payload)

    def get_feedr_job(self, job_id: str) -> Dict:
        """Poll Feedr scraping job status. Returns {status, result, error}."""
        return self._request("GET", f"/indexing/feedr/{job_id}", timeout=15.0)

    def parse_catering_file(self, file_bytes: bytes, filename: str) -> Dict:
        """Upload a PDF/DOCX/TXT and extract catering dietary groups, headcount, budget, location via LLM."""
        files = {"file": (filename, file_bytes, "application/octet-stream")}
        url = f"{self.base_url}/indexing/parse-catering-file"
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        with httpx.Client(timeout=LONG_TIMEOUT) as http:
            response = http.post(url, headers=headers, files=files)
        if not response.is_success:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise APIError(response.status_code, detail)
        return response.json()

    def match_catering_vendors(
        self,
        lat: float,
        lon: float,
        groups: List[Dict],
        budget: Optional[float] = None,
    ) -> Dict:
        """Find nearby Feedr vendors matched to dietary groups with estimated costs. Returns per-group matches."""
        payload: Dict[str, Any] = {"lat": lat, "lon": lon, "groups": groups}
        if budget is not None and budget > 0:
            payload["budget"] = budget
        return self._request("POST", "/indexing/catering-match", timeout=LONG_TIMEOUT, json=payload)

    def get_vendor_detail(self, permalink: str) -> Dict:
        """Fetch full vendor info + complete live menu from Feedr.co. Returns {name, images, menu_items, ...}."""
        return self._request(
            "GET",
            f"/indexing/vendors/detail/{permalink}",
            timeout=LONG_TIMEOUT,
        )

    def get_nearby_vendors(
        self,
        lat: float,
        lon: float,
        max_results: int = 20,
    ) -> Dict:
        """Fetch nearby catering vendors from Feedr.co sorted by distance. Returns {vendors, count}."""
        return self._request(
            "GET",
            "/indexing/vendors/nearby",
            timeout=LONG_TIMEOUT,
            params={"lat": lat, "lon": lon, "max_results": max_results},
        )

    def list_sources(self) -> List[Dict]:
        return self._request("GET", "/indexing/sources")

    def delete_source(self, source_id: int) -> None:
        self._request("DELETE", f"/indexing/sources/{source_id}")

    def list_collections(self) -> List[str]:
        data = self._request("GET", "/indexing/collections")
        return data.get("collections", [])

    # ── MCP Tools ────────────────────────────────────────────────────────────

    def mcp_list_tools(self) -> List[Dict]:
        return self._request("GET", "/mcp/tools")

    def mcp_list_prompts(self) -> List[Dict]:
        return self._request("GET", "/mcp/prompts")

    def mcp_call_tool(self, tool_name: str, arguments: Optional[Dict] = None) -> Dict:
        return self._request(
            "POST",
            "/mcp/call",
            timeout=LONG_TIMEOUT,
            json={"tool_name": tool_name, "arguments": arguments or {}},
        )

    def mcp_get_prompt(self, prompt_name: str, arguments: Optional[Dict] = None) -> str:
        data = self._request(
            "POST",
            "/mcp/prompt",
            json={"prompt_name": prompt_name, "arguments": arguments or {}},
        )
        return data["text"]

    def mcp_search_venues(
        self,
        city: str,
        categories: Optional[List[str]] = None,
        min_capacity: int = 0,
        radius_km: int = 5,
    ) -> Any:
        return self._request(
            "POST",
            "/mcp/venues/search",
            timeout=LONG_TIMEOUT,
            json={
                "city": city,
                "categories": categories or ["Conference & Event Venues"],
                "min_capacity": min_capacity,
                "radius_km": radius_km,
            },
        )

    def mcp_budget_planner(
        self,
        event_type: str,
        total_budget: float,
        currency: str = "GBP",
    ) -> Any:
        return self._request(
            "POST",
            "/mcp/budget",
            json={"event_type": event_type, "total_budget": total_budget, "currency": currency},
        )

    def mcp_catering_guide(self, event_type: str) -> Any:
        return self._request("GET", f"/mcp/catering/{event_type}")

    def mcp_geocode_city(self, city: str) -> Any:
        return self._request("GET", f"/mcp/geocode/{city}")

    def mcp_prompt_planning_checklist(self, event_type: str = "corporate") -> str:
        data = self._request("GET", "/mcp/prompts/planning-checklist", params={"event_type": event_type})
        return data["text"]

    def mcp_prompt_venue_rfp(
        self,
        event_type: str = "corporate",
        guest_count: int = 100,
        city: str = "London",
        event_date: str = "TBC",
        budget: str = "TBC",
    ) -> str:
        data = self._request(
            "GET",
            "/mcp/prompts/venue-rfp",
            params={
                "event_type": event_type,
                "guest_count": guest_count,
                "city": city,
                "event_date": event_date,
                "budget": budget,
            },
        )
        return data["text"]

    def mcp_prompt_budget_guide(
        self,
        event_type: str = "corporate",
        total_budget: str = "£10,000",
        guest_count: int = 100,
    ) -> str:
        data = self._request(
            "GET",
            "/mcp/prompts/budget-guide",
            params={"event_type": event_type, "total_budget": total_budget, "guest_count": guest_count},
        )
        return data["text"]

    def mcp_prompt_catering_brief(
        self,
        event_type: str = "corporate",
        guest_count: int = 100,
        dietary_notes: str = "",
    ) -> str:
        data = self._request(
            "GET",
            "/mcp/prompts/catering-brief",
            params={
                "event_type": event_type,
                "guest_count": guest_count,
                "dietary_notes": dietary_notes,
            },
        )
        return data["text"]

    # ── Health ────────────────────────────────────────────────────────────────

    def health(self) -> Dict:
        return self._request("GET", "/health/", timeout=30.0)

    def ping(self) -> Dict:
        return self._request("GET", "/health/ping", timeout=5.0)
