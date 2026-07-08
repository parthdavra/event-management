import streamlit as st
import plotly.graph_objects as go

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="Business Metrics — AI Event Manager", page_icon="📈", layout="wide")
require_auth()
show_sidebar()

st.title("📈 Business Metrics")
st.markdown("Platform-wide growth, engagement, and adoption metrics across all users.")

client = get_client()

# Palette (same validated categorical slots as Query Insights, fixed order — never cycled)
_BLUE = "#2a78d6"     # slot 1 — events
_AQUA = "#1baf7a"     # slot 2 — queries
_YELLOW = "#eda100"   # slot 3
_VIOLET = "#4a3aa7"   # slot 4
_PALETTE = [_BLUE, _AQUA, _YELLOW, _VIOLET]

try:
    summary = client.ai_business_summary()
    trend = client.ai_business_trend(days=30)
except APIError as exc:
    st.error(f"Could not load business metrics: {exc.detail}")
    st.stop()

# ── Headline stats ────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Users", f"{summary['total_users']:,}")
c2.metric("Total Events", f"{summary['total_events']:,}")
c3.metric("Total AI Queries", f"{summary['total_queries']:,}")
c4.metric("Indexed Sources", f"{summary['total_indexed_sources']:,}")
c5.metric("Indexed Chunks", f"{summary['total_indexed_chunks']:,}")

st.markdown("---")

if not summary.get("total_events") and not summary.get("total_queries"):
    st.info("No events or queries recorded yet — trends and adoption charts will appear once there's activity.")
else:
    # ── Growth trends ──────────────────────────────────────────────────────────
    st.markdown("### 📈 Growth Trends")
    st.caption("Events created and AI queries run, per day, over the last 30 days.")

    events_trend = trend["events_trend"]
    queries_trend = trend["queries_trend"]
    dates = [d["date"] for d in events_trend]

    col_a, col_b = st.columns(2)

    with col_a:
        fig_events = go.Figure()
        fig_events.add_trace(go.Scatter(
            x=dates, y=[d["count"] for d in events_trend], mode="lines+markers",
            line=dict(color=_BLUE, width=2),
            marker=dict(size=6, color=_BLUE),
            name="Events",
        ))
        fig_events.update_layout(
            title="Events Created / Day",
            height=320,
            margin=dict(l=40, r=20, t=40, b=30),
            showlegend=False,
            yaxis_title="Events",
        )
        st.plotly_chart(fig_events, use_container_width=True)

    with col_b:
        fig_queries = go.Figure()
        fig_queries.add_trace(go.Scatter(
            x=dates, y=[d["count"] for d in queries_trend], mode="lines+markers",
            line=dict(color=_AQUA, width=2),
            marker=dict(size=6, color=_AQUA),
            name="Queries",
        ))
        fig_queries.update_layout(
            title="AI Queries / Day",
            height=320,
            margin=dict(l=40, r=20, t=40, b=30),
            showlegend=False,
            yaxis_title="Queries",
        )
        st.plotly_chart(fig_queries, use_container_width=True)

    st.markdown("---")

    # ── Feature adoption ───────────────────────────────────────────────────────
    st.markdown("### 🧩 Feature Adoption")
    st.caption("Which AI endpoints get used — chat, RAG, or the multi-agent planner.")

    breakdown = summary.get("endpoint_breakdown") or []
    if not breakdown:
        st.info("No AI queries recorded yet.")
    else:
        fig_adoption = go.Figure()
        fig_adoption.add_trace(go.Bar(
            x=[b["count"] for b in breakdown],
            y=[b["endpoint"] for b in breakdown],
            orientation="h",
            marker=dict(color=[_PALETTE[i % len(_PALETTE)] for i in range(len(breakdown))]),
            text=[f"{b['count']:,}" for b in breakdown],
            textposition="outside",
        ))
        fig_adoption.update_layout(
            title="Queries by Endpoint",
            height=260,
            margin=dict(l=120, r=40, t=40, b=30),
            showlegend=False,
        )
        st.plotly_chart(fig_adoption, use_container_width=True)

    st.markdown("---")

    # ── Power users ────────────────────────────────────────────────────────────
    st.markdown("### 🏆 Power Users")

    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown("**By Events Created**")
        top_events = summary.get("top_users_by_events") or []
        if not top_events:
            st.caption("No events created yet.")
        else:
            st.dataframe(
                [{"Username": u["username"], "Events": u["count"]} for u in top_events],
                use_container_width=True,
                hide_index=True,
            )

    with col_d:
        st.markdown("**By AI Queries**")
        top_queries = summary.get("top_users_by_queries") or []
        if not top_queries:
            st.caption("No queries run yet.")
        else:
            st.dataframe(
                [{"Username": u["username"], "Queries": u["count"]} for u in top_queries],
                use_container_width=True,
                hide_index=True,
            )
