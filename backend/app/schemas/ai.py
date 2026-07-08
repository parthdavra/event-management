from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatHistoryMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatHistoryMessage]] = None


class RagRequest(BaseModel):
    query: str
    collection_names: Optional[List[str]] = None  # None → query all user collections
    n_results: int = 5
    chat_history: Optional[List[ChatHistoryMessage]] = None


class RagResponse(BaseModel):
    answer: str
    sources_used: List[str]
    confidence: str
    query_interpretation: str


class ExtractRequirementsResponse(BaseModel):
    event_name: str
    city: str
    location_hint: str
    radius_km: float
    categories: List[str]
    guest_count: Optional[int] = None
    budget: Optional[str] = None
    event_date: Optional[str] = None
    event_type: str
    collection_slug: str


class AgentRequest(BaseModel):
    query: str
    collection_name: str
    city: str = ""
    chat_history: Optional[List[ChatHistoryMessage]] = None


class AgentTraceEvent(BaseModel):
    type: str
    data: Optional[Dict[str, Any]] = None


class AIHistoryMessage(BaseModel):
    role: str
    content: str
    created_at: Any


class QueryMetricSummary(BaseModel):
    total_queries: int
    avg_latency_ms: float
    avg_prompt_tokens: float
    avg_completion_tokens: float
    avg_tokens: float
    avg_cost_usd: float
    total_cost_usd: float
    scored_queries: int
    avg_faithfulness: Optional[float] = None
    avg_answer_relevancy: Optional[float] = None
    avg_context_precision: Optional[float] = None


class QueryMetricRecent(BaseModel):
    endpoint: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    created_at: Any
    ragas_status: str
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    context_precision: Optional[float] = None


class DailyCount(BaseModel):
    date: str
    count: int


class EndpointCount(BaseModel):
    endpoint: str
    count: int


class TopUser(BaseModel):
    username: str
    count: int


class BusinessMetricsSummary(BaseModel):
    total_users: int
    total_events: int
    total_queries: int
    total_indexed_sources: int
    total_indexed_chunks: int
    endpoint_breakdown: List[EndpointCount]
    top_users_by_events: List[TopUser]
    top_users_by_queries: List[TopUser]


class BusinessMetricsTrend(BaseModel):
    events_trend: List[DailyCount]
    queries_trend: List[DailyCount]


class InputGuardrailResponse(BaseModel):
    allowed: bool
    rejection_reason: Optional[str] = None
    corrected_query: str
    was_corrected: bool
    corrections: List[Dict[str, str]]
    is_sarcastic: bool
    sarcasm_explanation: Optional[str] = None
    real_intent: str
    category: str
