"""
Platform-wide business/product metrics — Users, Events, QueryMetric, and
IndexedSource combined. Unlike query_metrics_service (per-user RAGAS/query
domain), everything here aggregates across all users: this app has no
admin/role concept, so business metrics follow the same convention as every
other page — visible to any authenticated user, not gated further.
"""

from datetime import datetime, timedelta
from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.indexed_source import IndexedSource
from app.models.query_metric import QueryMetric
from app.models.user import User


def get_growth_summary(db: Session) -> dict:
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_events = db.query(func.count(Event.id)).scalar() or 0
    total_queries = db.query(func.count(QueryMetric.id)).scalar() or 0
    total_sources, total_chunks = (
        db.query(func.count(IndexedSource.id), func.sum(IndexedSource.chunk_count))
        .filter(IndexedSource.status == "indexed")
        .one()
    )
    return {
        "total_users": total_users,
        "total_events": total_events,
        "total_queries": total_queries,
        "total_indexed_sources": total_sources or 0,
        "total_indexed_chunks": total_chunks or 0,
    }


def _daily_counts(db: Session, model, date_column, days: int) -> List[dict]:
    """Day-bucketed count, zero-filled so trend charts have no gaps."""
    since = datetime.utcnow() - timedelta(days=days - 1)
    bucket = func.date_trunc("day", date_column).label("day")
    rows = (
        db.query(bucket, func.count(model.id))
        .filter(date_column >= since)
        .group_by(bucket)
        .order_by(bucket)
        .all()
    )
    counts = {r[0].date(): r[1] for r in rows}
    out = []
    for i in range(days):
        d = (since + timedelta(days=i)).date()
        out.append({"date": d.isoformat(), "count": counts.get(d, 0)})
    return out


def get_events_trend(db: Session, days: int = 30) -> List[dict]:
    return _daily_counts(db, Event, Event.created_at, days)


def get_queries_trend(db: Session, days: int = 30) -> List[dict]:
    return _daily_counts(db, QueryMetric, QueryMetric.created_at, days)


def get_endpoint_breakdown(db: Session) -> List[dict]:
    """Feature adoption — which AI endpoints get used (/ai/rag, /ai/agent, /ai/chat)."""
    rows = (
        db.query(QueryMetric.endpoint, func.count(QueryMetric.id))
        .group_by(QueryMetric.endpoint)
        .order_by(func.count(QueryMetric.id).desc())
        .all()
    )
    return [{"endpoint": e, "count": c} for e, c in rows]


def get_top_users_by_events(db: Session, limit: int = 5) -> List[dict]:
    rows = (
        db.query(User.username, func.count(Event.id).label("n"))
        .join(Event, Event.user_id == User.id)
        .group_by(User.id, User.username)
        .order_by(func.count(Event.id).desc())
        .limit(limit)
        .all()
    )
    return [{"username": u, "count": c} for u, c in rows]


def get_top_users_by_queries(db: Session, limit: int = 5) -> List[dict]:
    rows = (
        db.query(User.username, func.count(QueryMetric.id).label("n"))
        .join(QueryMetric, QueryMetric.user_id == User.id)
        .group_by(User.id, User.username)
        .order_by(func.count(QueryMetric.id).desc())
        .limit(limit)
        .all()
    )
    return [{"username": u, "count": c} for u, c in rows]


def get_business_summary(db: Session, top_n: int = 5) -> dict:
    """Thin orchestrator, matching how ai.py calls query_metrics_service.get_summary()."""
    return {
        **get_growth_summary(db),
        "endpoint_breakdown": get_endpoint_breakdown(db),
        "top_users_by_events": get_top_users_by_events(db, top_n),
        "top_users_by_queries": get_top_users_by_queries(db, top_n),
    }
