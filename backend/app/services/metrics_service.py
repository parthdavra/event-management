"""
CloudWatch custom metrics for system-level observability (request volume,
latency, error rate, LLM token usage / estimated cost). Complements
Langfuse's per-trace LLM tracing with an aggregate, infra-level view shown
on a CloudWatch Dashboard.

Metric pushes are fire-and-forget on a background thread pool so they never
add latency to the request path; failures (e.g. an expired AWS SSO session)
are swallowed rather than surfaced to users.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from app.core.config import get_settings

settings = get_settings()

_executor = ThreadPoolExecutor(max_workers=4)

# Per-request token/cost accumulator, used to attribute LLM usage (which can span
# several calls — guardrails, query rewrite, generation) back to the single
# user-facing query that triggered them. See start_query_capture()/get_query_capture().
_query_capture: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_query_capture", default=None
)


def start_query_capture() -> None:
    _query_capture.set({"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})


def get_query_capture() -> dict:
    return _query_capture.get() or {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

NAMESPACE = "EventManagement"

# Rough USD-per-1K-token estimates for system-level cost tracking.
# Not exact billing — Langfuse's per-trace cost is the source of truth for
# precise LLM cost; this is an aggregate approximation for the ops dashboard.
_PRICING_PER_1K = {
    "gpt-4.1-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "text-embedding-3-small": {"prompt": 0.00002, "completion": 0.0},
}
_DEFAULT_PRICING = {"prompt": 0.0005, "completion": 0.0015}


def _client():
    import boto3
    return boto3.client(
        "cloudwatch",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        aws_session_token=settings.aws_session_token or None,
    )


def _put(metric_data: List[dict]) -> None:
    try:
        _client().put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
    except Exception:
        pass  # Never let metrics failures (e.g. expired SSO session) affect the app


def record_request(path: str, method: str, status_code: int, duration_ms: float) -> None:
    is_error = status_code >= 400
    dims = [{"Name": "Endpoint", "Value": path}]
    data = [
        {"MetricName": "RequestCount", "Value": 1, "Unit": "Count", "Dimensions": dims},
        {"MetricName": "Latency", "Value": duration_ms, "Unit": "Milliseconds", "Dimensions": dims},
        {"MetricName": "ErrorCount", "Value": 1 if is_error else 0, "Unit": "Count", "Dimensions": dims},
    ]
    _executor.submit(_put, data)


def record_llm_usage(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    pricing = _PRICING_PER_1K.get(model, _DEFAULT_PRICING)
    cost = (prompt_tokens / 1000) * pricing["prompt"] + (completion_tokens / 1000) * pricing["completion"]
    data = [
        {"MetricName": "PromptTokens", "Value": float(prompt_tokens), "Unit": "Count"},
        {"MetricName": "CompletionTokens", "Value": float(completion_tokens), "Unit": "Count"},
        {"MetricName": "EstimatedCostUSD", "Value": cost, "Unit": "None"},
    ]
    _executor.submit(_put, data)

    acc = _query_capture.get()
    if acc is not None:
        acc["prompt_tokens"] += prompt_tokens
        acc["completion_tokens"] += completion_tokens
        acc["cost_usd"] += cost
