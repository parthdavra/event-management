import streamlit as st
import plotly.graph_objects as go

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="Query Insights — AI Event Manager", page_icon="📊", layout="wide")
require_auth()
show_sidebar()

st.title("📊 Query Insights")
st.markdown("Per-query cost, latency, and token usage for your AI Assistant conversations.")

client = get_client()

# Palette (validated categorical slots, fixed order — never cycled)
_BLUE = "#2a78d6"     # slot 1 — cost
_AQUA = "#1baf7a"     # slot 2 — latency / answer relevancy
_YELLOW = "#eda100"   # slot 3 — faithfulness
_VIOLET = "#4a3aa7"   # slot 5 — context precision

try:
    summary = client.ai_metrics_summary()
    recent = client.ai_metrics_recent(limit=30)
except APIError as exc:
    st.error(f"Could not load query metrics: {exc.detail}")
    st.stop()

if not summary.get("total_queries"):
    st.info("No AI queries recorded yet. Ask something on the AI Assistant page, then come back here.")
    st.stop()

# ── Headline stats ────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Queries", f"{summary['total_queries']:,}")
c2.metric("Avg Latency", f"{summary['avg_latency_ms']:.0f} ms")
c3.metric("Avg Tokens / Query", f"{summary['avg_tokens']:.0f}")
c4.metric("Avg Cost / Query", f"${summary['avg_cost_usd']:.5f}")
c5.metric("Total Est. Cost", f"${summary['total_cost_usd']:.4f}")

st.markdown("---")

# ── RAGAS quality scores ──────────────────────────────────────────────────────
st.markdown("### 🧪 RAGAS Quality Scores")
st.caption(
    "Computed asynchronously in the background after each answer is returned (no reference/ground-truth "
    "needed). **Faithfulness** and **Context Precision** only apply to RAG queries that retrieve context; "
    "**Answer Relevancy** applies to every query. Classic RAGAS Context Recall needs a ground-truth answer "
    "and isn't computed live — that lives in the separate golden-dataset batch evaluation."
)

if not summary.get("scored_queries"):
    st.info("No queries have finished RAGAS scoring yet — scores appear here within a few seconds of asking a question.")
else:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Scored Queries", f"{summary['scored_queries']:,} / {summary['total_queries']:,}")
    r2.metric(
        "Avg Faithfulness",
        f"{summary['avg_faithfulness']:.2f}" if summary.get("avg_faithfulness") is not None else "—",
    )
    r3.metric(
        "Avg Answer Relevancy",
        f"{summary['avg_answer_relevancy']:.2f}" if summary.get("avg_answer_relevancy") is not None else "—",
    )
    r4.metric(
        "Avg Context Precision",
        f"{summary['avg_context_precision']:.2f}" if summary.get("avg_context_precision") is not None else "—",
    )

    fig_ragas = go.Figure()
    metric_names = ["Faithfulness", "Answer Relevancy", "Context Precision"]
    metric_values = [
        summary.get("avg_faithfulness") or 0,
        summary.get("avg_answer_relevancy") or 0,
        summary.get("avg_context_precision") or 0,
    ]
    fig_ragas.add_trace(go.Bar(
        x=metric_values, y=metric_names, orientation="h",
        marker=dict(color=[_YELLOW, _AQUA, _VIOLET]),
        text=[f"{v:.2f}" for v in metric_values],
        textposition="outside",
    ))
    fig_ragas.update_layout(
        title="Average RAGAS Scores",
        height=260,
        margin=dict(l=120, r=40, t=40, b=30),
        xaxis=dict(range=[0, 1.05], title="Score (0–1)"),
        showlegend=False,
    )
    st.plotly_chart(fig_ragas, use_container_width=True)

st.markdown("---")

# ── Trend charts (most recent first → reverse for chronological left-to-right) ─
ordered = list(reversed(recent))
labels = [f"#{i + 1}" for i in range(len(ordered))]
costs = [r["cost_usd"] for r in ordered]
latencies = [r["latency_ms"] for r in ordered]

col_a, col_b = st.columns(2)

with col_a:
    fig_cost = go.Figure()
    fig_cost.add_trace(go.Scatter(
        x=labels, y=costs, mode="lines+markers",
        line=dict(color=_BLUE, width=2),
        marker=dict(size=8, color=_BLUE),
        name="Cost (USD)",
    ))
    fig_cost.update_layout(
        title="Cost per Query (USD)",
        height=320,
        margin=dict(l=40, r=20, t=40, b=30),
        showlegend=False,
        yaxis_title="USD",
    )
    st.plotly_chart(fig_cost, use_container_width=True)

with col_b:
    fig_latency = go.Figure()
    fig_latency.add_trace(go.Scatter(
        x=labels, y=latencies, mode="lines+markers",
        line=dict(color=_AQUA, width=2),
        marker=dict(size=8, color=_AQUA),
        name="Latency (ms)",
    ))
    fig_latency.update_layout(
        title="Latency per Query (ms)",
        height=320,
        margin=dict(l=40, r=20, t=40, b=30),
        showlegend=False,
        yaxis_title="Milliseconds",
    )
    st.plotly_chart(fig_latency, use_container_width=True)

st.markdown("---")

# ── Recent queries table ───────────────────────────────────────────────────────
def _fmt_score(v):
    return round(v, 3) if v is not None else None


st.markdown("### Recent Queries")
st.dataframe(
    [
        {
            "Time": r["created_at"],
            "Endpoint": r["endpoint"],
            "Latency (ms)": round(r["latency_ms"], 1),
            "Prompt Tokens": r["prompt_tokens"],
            "Completion Tokens": r["completion_tokens"],
            "Cost (USD)": round(r["cost_usd"], 6),
            "RAGAS Status": "⏳ pending" if r["ragas_status"] == "pending" else r["ragas_status"],
            "Faithfulness": _fmt_score(r["faithfulness"]),
            "Answer Relevancy": _fmt_score(r["answer_relevancy"]),
            "Context Precision": _fmt_score(r["context_precision"]),
        }
        for r in recent
    ],
    use_container_width=True,
    hide_index=True,
)
