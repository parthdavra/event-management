"""Shared Streamlit UI helpers used across multiple pages."""
import streamlit as st

_SOURCE_ICONS: dict[str, str] = {
    "Canvas Events":   "🎪",
    "Foursquare":      "📍",
    "OpenStreetMap":   "🗺️",
    "Geoapify":        "🌐",
    "Indexed Chunks":  "🗄️",
}

_TOOL_ICONS: dict[str, str] = {
    "mcp":    "🟢",
    "local":  "🔵",
    "chunks": "🗄️",
}

_TOOL_LABELS: dict[str, str] = {
    "mcp":    "MCP Tool",
    "local":  "Local API",
    "chunks": "Indexed Chunks",
}


def tool_badge(tool_source: str, tool_name: str) -> None:
    """Show which backend tool served the request."""
    icon = _TOOL_ICONS.get(tool_source, "🔵")
    label = _TOOL_LABELS.get(tool_source, "Local")
    st.caption(f"{icon} **{label}:** `{tool_name}`")


def data_source_badges(source_counts: dict[str, int]) -> None:
    """
    Show which external APIs contributed venues and how many each returned.

    Example:
        🎪 Canvas Events: 45  ·  📍 Foursquare: 12  ·  🗺️ OpenStreetMap: 8
    """
    if not source_counts:
        return
    parts = [
        f"{_SOURCE_ICONS.get(src, '📊')} **{src}:** {cnt}"
        for src, cnt in source_counts.items()
        if cnt > 0
    ]
    zero_parts = [
        f"~~{src}: 0~~"
        for src, cnt in source_counts.items()
        if cnt == 0
    ]
    all_parts = parts + zero_parts
    if all_parts:
        st.caption("**Data sources:** " + "  ·  ".join(all_parts))
