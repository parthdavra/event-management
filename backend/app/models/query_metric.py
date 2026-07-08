from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class QueryMetric(Base):
    """One row per user-facing AI query (chat/rag/agent) — latency, token usage,
    and estimated cost, aggregated across every LLM call that query triggered."""

    __tablename__ = "query_metrics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(String, nullable=False)
    latency_ms = Column(Float, nullable=False)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # RAGAS scores — computed asynchronously after the response is sent (see
    # app.services.ragas_service), so these are NULL until scoring completes.
    # context_precision/faithfulness require retrieved_contexts and are only
    # populated for /ai/rag; answer_relevancy needs no context and is computed
    # for every endpoint that has an answer.
    faithfulness = Column(Float, nullable=True)
    answer_relevancy = Column(Float, nullable=True)
    context_precision = Column(Float, nullable=True)
    ragas_status = Column(String, default="pending")  # pending | scored | failed

    user = relationship("User")
