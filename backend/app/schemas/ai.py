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
