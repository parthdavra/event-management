from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.ai import (
    AgentRequest,
    ChatRequest,
    ExtractRequirementsResponse,
    InputGuardrailResponse,
    RagRequest,
    RagResponse,
)
from app.services import agent_service, guardrails_service, indexing_service, rag_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/chat")
def direct_chat(
    body: ChatRequest,
    _: User = Depends(get_current_user),
) -> Dict[str, str]:
    """Direct LLM chat (no RAG context)."""
    history = [m.model_dump() for m in (body.chat_history or [])]
    try:
        answer = rag_service.generate_chat_response(body.query, history)
        return {"answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post("/rag", response_model=RagResponse)
def rag_chat(
    body: RagRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """RAG-powered chat: retrieve context from ChromaDB, then answer."""
    history = [m.model_dump() for m in (body.chat_history or [])]
    try:
        if body.collection_names:
            collections = body.collection_names
        else:
            collections = indexing_service.get_all_user_collections(db, current_user.id)

        # Rewrite the query to be self-contained using conversation history,
        # so ChromaDB retrieval resolves references like "the second venue" correctly.
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
def run_agent(
    body: AgentRequest,
    _: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """
    Run the event planning agent and return all trace events as a list.
    For streaming use cases, the frontend can poll or use SSE.
    Returns a list of trace event dicts (type: thinking|tool_call|tool_result|answer|error).
    """
    history = [m.model_dump() for m in (body.chat_history or [])]
    try:
        events = list(
            agent_service.run_agent(
                query=body.query,
                collection_name=body.collection_name,
                city=body.city,
                chat_history=history,
            )
        )
        return events
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
