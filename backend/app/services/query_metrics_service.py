from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.query_metric import QueryMetric


def save(
    db: Session,
    user_id: int,
    endpoint: str,
    latency_ms: float,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> int:
    row = QueryMetric(
        user_id=user_id,
        endpoint=endpoint,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def update_ragas_scores(db: Session, query_metric_id: int, scores: dict) -> None:
    row = db.query(QueryMetric).filter(QueryMetric.id == query_metric_id).first()
    if not row:
        return
    # RAGAS returns numpy.float64, not a plain float. psycopg2 has no adapter
    # for it and serializes it as the literal text "np.float64(...)", which
    # Postgres rejects as an invalid schema-qualified function call — cast to
    # plain float so the value round-trips through psycopg2 correctly.
    if "faithfulness" in scores and scores["faithfulness"] is not None:
        row.faithfulness = float(scores["faithfulness"])
    if "answer_relevancy" in scores and scores["answer_relevancy"] is not None:
        row.answer_relevancy = float(scores["answer_relevancy"])
    if "context_precision" in scores and scores["context_precision"] is not None:
        row.context_precision = float(scores["context_precision"])
    row.ragas_status = "scored" if scores else "failed"
    db.commit()


def get_recent(db: Session, user_id: int, limit: int = 20) -> List[dict]:
    rows = (
        db.query(QueryMetric)
        .filter(QueryMetric.user_id == user_id)
        .order_by(QueryMetric.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_to_dict(r) for r in rows]


def get_summary(db: Session, user_id: int) -> dict:
    total, avg_latency, avg_prompt, avg_completion, avg_cost, total_cost = (
        db.query(
            func.count(QueryMetric.id),
            func.avg(QueryMetric.latency_ms),
            func.avg(QueryMetric.prompt_tokens),
            func.avg(QueryMetric.completion_tokens),
            func.avg(QueryMetric.cost_usd),
            func.sum(QueryMetric.cost_usd),
        )
        .filter(QueryMetric.user_id == user_id)
        .one()
    )
    # RAGAS scores are only present on rows that have finished background scoring —
    # average over non-null values only, so pending/failed rows don't skew the mean.
    avg_faithfulness, avg_answer_relevancy, avg_context_precision, scored_count = (
        db.query(
            func.avg(QueryMetric.faithfulness),
            func.avg(QueryMetric.answer_relevancy),
            func.avg(QueryMetric.context_precision),
            func.count(QueryMetric.id).filter(QueryMetric.ragas_status == "scored"),
        )
        .filter(QueryMetric.user_id == user_id)
        .one()
    )
    avg_tokens = (avg_prompt or 0.0) + (avg_completion or 0.0)
    return {
        "total_queries": total or 0,
        "avg_latency_ms": round(avg_latency, 1) if avg_latency else 0.0,
        "avg_prompt_tokens": round(avg_prompt, 1) if avg_prompt else 0.0,
        "avg_completion_tokens": round(avg_completion, 1) if avg_completion else 0.0,
        "avg_tokens": round(avg_tokens, 1),
        "avg_cost_usd": round(avg_cost, 6) if avg_cost else 0.0,
        "total_cost_usd": round(total_cost, 6) if total_cost else 0.0,
        "scored_queries": scored_count or 0,
        "avg_faithfulness": round(avg_faithfulness, 3) if avg_faithfulness else None,
        "avg_answer_relevancy": round(avg_answer_relevancy, 3) if avg_answer_relevancy else None,
        "avg_context_precision": round(avg_context_precision, 3) if avg_context_precision else None,
    }


def _to_dict(r: QueryMetric) -> dict:
    return {
        "endpoint": r.endpoint,
        "latency_ms": round(r.latency_ms, 1),
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "cost_usd": r.cost_usd,
        "created_at": r.created_at,
        "ragas_status": r.ragas_status,
        "faithfulness": r.faithfulness,
        "answer_relevancy": r.answer_relevancy,
        "context_precision": r.context_precision,
    }
