import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from langfuse import observe
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.user import User
from app.schemas.ai import (
    AgentRequest,
    AIHistoryMessage,
    BusinessMetricsSummary,
    BusinessMetricsTrend,
    ChatRequest,
    ExtractRequirementsResponse,
    InputGuardrailResponse,
    QueryMetricRecent,
    QueryMetricSummary,
    RagRequest,
    RagResponse,
)
from app.services import (
    agent_service,
    ai_chat_service,
    business_metrics_service,
    guardrails_service,
    indexing_service,
    metrics_service,
    query_metrics_service,
    ragas_service,
    rag_service,
)

router = APIRouter(prefix="/ai", tags=["ai"])
settings = get_settings()


def _record_langfuse_query_metrics(endpoint: str, latency_ms: float, usage: dict, total_tokens: int) -> None:
    """
    Surface per-query cost/token/latency in Langfuse. Scores (not just metadata)
    so Langfuse's own UI aggregates avg/trend across queries natively, without
    needing our own dashboard for it. Must be called from within the active
    @observe span for the current endpoint — score_current_trace/update_current_span
    attach to whatever trace is currently in context.
    """
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return
    try:
        from langfuse import get_client
        client = get_client()
        client.update_current_span(metadata={
            "endpoint": endpoint,
            "cost_usd": usage["cost_usd"],
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 1),
        })
        client.score_current_trace(name="cost_usd", value=usage["cost_usd"], data_type="NUMERIC")
        client.score_current_trace(name="total_tokens", value=total_tokens, data_type="NUMERIC")
        client.score_current_trace(name="latency_ms", value=round(latency_ms, 1), data_type="NUMERIC")
    except Exception:
        pass  # Never let Langfuse instrumentation break the actual request


def _save_query_metric(
    db: Session,
    user_id: int,
    endpoint: str,
    t0: float,
    query: str,
    answer: str,
    contexts: Optional[List[str]] = None,
) -> None:
    latency_ms = (time.time() - t0) * 1000
    usage = metrics_service.get_query_capture()
    total_tokens = usage["prompt_tokens"] + usage["completion_tokens"]
    metric_id = query_metrics_service.save(
        db, user_id, endpoint, latency_ms,
        usage["prompt_tokens"], usage["completion_tokens"], usage["cost_usd"],
    )
    ragas_service.score_query_in_background(metric_id, query, answer, contexts)
    _record_langfuse_query_metrics(endpoint, latency_ms, usage, total_tokens)


@router.post("/chat")
@observe(name="chat_query")
def direct_chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """Direct LLM chat (no RAG context)."""
    history = [m.model_dump() for m in (body.chat_history or [])]
    t0 = time.time()
    metrics_service.start_query_capture()
    try:
        answer = rag_service.generate_chat_response(body.query, history)
        _save_query_metric(db, current_user.id, "/ai/chat", t0, body.query, answer)
        ai_chat_service.save_message(db, current_user.id, "user", body.query)
        ai_chat_service.save_message(db, current_user.id, "assistant", answer)
        return {"answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/rag", response_model=RagResponse)
@observe(name="rag_query")
def rag_chat(
    body: RagRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """RAG-powered chat: retrieve context from OpenSearch, then answer."""
    history = [m.model_dump() for m in (body.chat_history or [])]
    t0 = time.time()
    metrics_service.start_query_capture()
    try:
        if body.collection_names:
            collections = body.collection_names
        else:
            collections = indexing_service.get_all_user_collections(db, current_user.id)

        # Rewrite the query to be self-contained using conversation history,
        # so OpenSearch retrieval resolves references like "the second venue" correctly.
        search_query = rag_service.rewrite_query_with_history(body.query, history)

        # Parse capacity / budget constraints from the query for smart metadata filtering
        constraints = rag_service.parse_query_constraints(search_query)
        min_cap = constraints.get("capacity")
        # Capacity window: requested N → show N to N+150 (hard cap at 400 when N <= 250)
        max_cap = None
        if min_cap is not None:
            window = 150 if min_cap > 250 else (400 - min_cap)
            max_cap = min_cap + max(window, 0)

        n_per = max(body.n_results or 5, 8)  # at least 8 for richer retrieval
        docs = rag_service.query_with_smart_filters(
            collections,
            search_query,
            n_per_collection=n_per,
            min_capacity=min_cap,
            max_capacity=max_cap,
            max_budget=constraints.get("budget"),
        )
        # Generate the answer using the ORIGINAL user query + full history for natural response
        result = rag_service.generate_rag_response_json(body.query, docs, history)
        _save_query_metric(db, current_user.id, "/ai/rag", t0, body.query, result.get("answer", ""), docs)
        ai_chat_service.save_message(db, current_user.id, "user", body.query)
        ai_chat_service.save_message(db, current_user.id, "assistant", result.get("answer", ""))
        return RagResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/extract-requirements", response_model=ExtractRequirementsResponse)
def extract_requirements(
    body: ChatRequest,
    _: User = Depends(get_current_user),
):
    """Use the LLM to extract structured event requirements from free text."""
    try:
        result = rag_service.extract_event_requirements(body.query)
        return ExtractRequirementsResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/check-input", response_model=InputGuardrailResponse)
def check_input(
    body: ChatRequest,
    _: User = Depends(get_current_user),
):
    """Run input guardrails (abuse detection, spell correction, sarcasm)."""
    try:
        result = guardrails_service.check_input(body.query)
        return InputGuardrailResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/agent")
@observe(name="agent_query")
def run_agent(
    body: AgentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    Run the event planning agent and return all trace events as a list.
    For streaming use cases, the frontend can poll or use SSE.
    Returns a list of trace event dicts (type: thinking|tool_call|tool_result|answer|error).
    """
    history = [m.model_dump() for m in (body.chat_history or [])]
    t0 = time.time()
    metrics_service.start_query_capture()
    try:
        events = list(
            agent_service.run_agent(
                query=body.query,
                collection_name=body.collection_name,
                city=body.city,
                chat_history=history,
            )
        )
        final_answer = next(
            (e["data"].get("answer", "") for e in reversed(events) if e.get("type") == "answer"),
            "",
        )
        _save_query_metric(db, current_user.id, "/ai/agent", t0, body.query, final_answer)
        ai_chat_service.save_message(db, current_user.id, "user", body.query)
        if final_answer:
            ai_chat_service.save_message(db, current_user.id, "assistant", final_answer)
        return events
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/history", response_model=List[AIHistoryMessage])
def get_ai_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current user's persisted AI Assistant conversation."""
    return ai_chat_service.get_history(db, current_user.id)


@router.delete("/history")
def clear_ai_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Clear the current user's persisted AI Assistant conversation."""
    ai_chat_service.clear_history(db, current_user.id)
    return {"ok": True}


@router.get("/metrics/summary", response_model=QueryMetricSummary)
def get_query_metrics_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate per-query stats (avg latency/tokens/cost, totals) for the current user."""
    return query_metrics_service.get_summary(db, current_user.id)


@router.get("/metrics/recent", response_model=List[QueryMetricRecent])
def get_query_metrics_recent(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Most recent individual queries (endpoint, latency, tokens, cost) for the current user."""
    return query_metrics_service.get_recent(db, current_user.id, limit=limit)


@router.get("/metrics/business-summary", response_model=BusinessMetricsSummary)
def get_business_metrics_summary(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Platform-wide business metrics — growth, feature adoption, power users.
    Aggregates across ALL users; this app has no admin/role concept, so any
    authenticated user can view it, same as every other page."""
    return business_metrics_service.get_business_summary(db)


@router.get("/metrics/business-trend", response_model=BusinessMetricsTrend)
def get_business_metrics_trend(
    days: int = 30,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Events-created and queries-run, bucketed by day, for the last N days."""
    return {
        "events_trend": business_metrics_service.get_events_trend(db, days),
        "queries_trend": business_metrics_service.get_queries_trend(db, days),
    }
