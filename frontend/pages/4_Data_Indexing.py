import streamlit as st

from utils.auth import require_auth, show_sidebar, get_client
from utils.ui import tool_badge, data_source_badges
from client.api_client import APIError

st.set_page_config(page_title="Data Indexing — AI Event Manager", page_icon="🗂️", layout="wide")
require_auth()
show_sidebar()

st.title("🗂️ Data Indexing")

client = get_client()

ALL_CATEGORIES = [
    "Restaurants & Cafes",
    "Bars & Nightlife",
    "Hotels & Accommodation",
    "Conference & Event Venues",
    "Arts & Entertainment",
    "Sports & Recreation",
    "Attractions & Tourism",
]


def render_source_card(src: dict):
    col_info, col_act = st.columns([3, 1])
    with col_info:
        st.markdown(f"**Chunks:** {src['chunk_count']}")
        st.markdown(f"**Status:** {src['status']}")
        if src.get("indexed_at"):
            st.markdown(f"**Indexed:** {src['indexed_at']}")
        st.caption(f"Collection: `{src['collection_name']}`")
    with col_act:
        if st.button("🗑️ Delete", key=f"del_src_{src['id']}"):
            st.session_state[f"confirm_src_{src['id']}"] = True
            st.rerun()

    if st.session_state.get(f"confirm_src_{src['id']}"):
        st.warning("Permanently delete this source and all its indexed chunks?")
        y, n = st.columns(2)
        with y:
            if st.button("Yes, Delete", key=f"yes_src_{src['id']}", type="primary"):
                try:
                    client.delete_source(src["id"])
                    st.session_state.pop(f"confirm_src_{src['id']}", None)
                    st.rerun()
                except APIError as exc:
                    st.error(exc.detail)
        with n:
            if st.button("Cancel", key=f"no_src_{src['id']}"):
                st.session_state.pop(f"confirm_src_{src['id']}", None)
                st.rerun()


tab_api, tab_sources, tab_add, tab_preview = st.tabs([
    "🌍 Fetch from APIs", "📋 Indexed Sources", "📄 Add Document", "🔍 Search Preview",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Fetch from APIs
# ═══════════════════════════════════════════════════════════════════════════════
with tab_api:
    st.subheader("Fetch & Index Venue Data by City")
    st.markdown(
        "Search real venue data from public APIs. "
        "Each city gets its **own ChromaDB collection** so the AI can answer location-specific questions."
    )

    col_city, col_radius = st.columns([3, 1])
    with col_city:
        city_input = st.text_input("City *", placeholder="e.g. London, New York, Sydney")
    with col_radius:
        radius_km = st.slider("Radius (km)", 1, 20, 5)

    selected_cats = st.multiselect(
        "Venue Categories *",
        options=ALL_CATEGORIES,
        default=["Restaurants & Cafes", "Conference & Event Venues", "Hotels & Accommodation"],
    )

    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        use_fsq = st.toggle("Use Foursquare", value=True)
    with col_t2:
        use_geo = st.toggle("Use Geoapify", value=True)
    with col_t3:
        max_venues = st.number_input("Max venues", min_value=10, max_value=1000, value=300, step=50)

    fetch_clicked = st.button(
        "🔍 Fetch Venues",
        type="primary",
        disabled=not city_input.strip() or not selected_cats,
    )

    if fetch_clicked:
        with st.spinner(f"Querying APIs for '{city_input}'…"):
            try:
                result = client.search_venues(
                    city=city_input.strip(),
                    categories=selected_cats,
                    radius_km=radius_km,
                    use_foursquare=use_fsq,
                    use_geoapify=use_geo,
                    enrich_details=True,
                    max_venues=int(max_venues),
                )
                st.session_state["fetched_venues"] = result["venues"]
                st.session_state["fetched_city"] = city_input.strip()
                st.session_state["fetch_source_counts"] = result["source_counts"]
                st.session_state["fetch_tool_source"] = result.get("tool_source", "local")
                st.session_state["fetch_tool_name"] = result.get("tool_name", "venue_service")
            except APIError as exc:
                st.error(f"Error: {exc.detail}")

    if st.session_state.get("fetched_venues") is not None:
        venues = st.session_state["fetched_venues"]
        city = st.session_state["fetched_city"]
        counts = st.session_state.get("fetch_source_counts", {})
        _ts = st.session_state.get("fetch_tool_source", "local")
        _tn = st.session_state.get("fetch_tool_name", "venue_service")

        if not venues:
            st.warning("No venues found. Try a larger radius or more categories.")
        else:
            st.markdown("---")
            st.markdown(f"### Preview — **{city}** ({len(venues)} unique venues found)")
            tool_badge(_ts, _tn)
            data_source_badges(counts)

            with st.expander("👀 Sample venues (first 20)"):
                def _price_summary(v: dict) -> str:
                    parts = []
                    if v.get("price_per_day"):
                        parts.append(f"Day: {v['price_per_day']}")
                    if v.get("price_per_hour"):
                        parts.append(f"Hourly: {v['price_per_hour']}")
                    if v.get("price_range") and not parts:
                        parts.append(v["price_range"])
                    if v.get("min_spend"):
                        parts.append(f"Min: {v['min_spend']}")
                    return " · ".join(parts) if parts else "—"

                rows = [
                    {
                        "Name": v["name"],
                        "Type": v.get("type") or "—",
                        "Address": v.get("address") or "—",
                        "Price": _price_summary(v),
                        "Source": v.get("source") or "—",
                    }
                    for v in venues[:20]
                ]
                st.dataframe(rows, use_container_width=True)

            st.markdown("---")
            replace_existing = st.checkbox("Replace existing collection for this city", value=True)

            if st.button(f"⚡ Index {len(venues)} venues into **'{city}'** collection", type="primary"):
                with st.spinner(f"Embedding & indexing {len(venues)} venues…"):
                    try:
                        result = client.index_city(city=city, venues=venues, replace_existing=replace_existing)
                        st.success(
                            f"✅ **{city}** indexed! {len(venues)} venues are now searchable."
                        )
                        st.session_state.pop("fetched_venues", None)
                        st.session_state.pop("fetched_city", None)
                        st.session_state.pop("fetch_source_counts", None)
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Indexing failed: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Indexed Sources
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sources:
    try:
        sources = client.list_sources()
    except APIError:
        sources = []

    if not sources:
        st.info("No indexed sources yet. Use 'Fetch from APIs' or 'Add Document'.")
    else:
        total_chunks = sum(s["chunk_count"] for s in sources)
        indexed_ok = sum(1 for s in sources if s["status"] == "indexed")
        city_cnt = sum(1 for s in sources if s["source_type"] == "city_api")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Sources", len(sources))
        c2.metric("Indexed", indexed_ok)
        c3.metric("Total Chunks", total_chunks)
        c4.metric("City Collections", city_cnt)
        st.markdown("---")

        city_sources = [s for s in sources if s["source_type"] == "city_api"]
        doc_sources = [s for s in sources if s["source_type"] != "city_api"]

        if city_sources:
            st.markdown("#### 🌍 City Collections")
            for src in city_sources:
                icon = {"indexed": "✅", "failed": "❌", "pending": "⏳"}.get(src["status"], "❓")
                city_label = src["source_name"].replace(" — City API Data", "")
                with st.expander(f"{icon} **{city_label}** — {src['chunk_count']} venues"):
                    render_source_card(src)

        if doc_sources:
            st.markdown("#### 📄 Document Sources")
            for src in doc_sources:
                icon = {"indexed": "✅", "failed": "❌", "pending": "⏳"}.get(src["status"], "❓")
                with st.expander(f"{icon} **{src['source_name']}** — {(src['source_type'] or 'doc').upper()}"):
                    render_source_card(src)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Add Document
# ═══════════════════════════════════════════════════════════════════════════════
with tab_add:
    st.subheader("Index a Document or Text")
    method = st.radio("Input Method", ["File Upload", "Raw Text"], horizontal=True)

    if method == "File Upload":
        uploaded = st.file_uploader(
            "Upload a document",
            type=["pdf", "docx", "txt"],
            help="Supported: PDF, DOCX, TXT (max 50 MB)",
        )
        name_override = st.text_input("Source Name (optional)", placeholder="Defaults to filename")

        if st.button("Index Document", type="primary", disabled=uploaded is None) and uploaded:
            name = name_override.strip() or uploaded.name
            file_bytes = uploaded.read()
            with st.spinner(f"Indexing '{name}'…"):
                try:
                    client.upload_document(file_bytes, uploaded.name, source_name=name if name != uploaded.name else None)
                    st.success(f"✅ '{name}' indexed!")
                    st.rerun()
                except APIError as exc:
                    st.error(f"Failed: {exc.detail}")

    else:
        raw_name = st.text_input("Source Name *", placeholder="e.g. Meeting Notes July 2026")
        raw_text = st.text_area("Paste Text *", height=300, placeholder="Paste content here…")
        if st.button("Index Text", type="primary"):
            if raw_name and raw_text:
                with st.spinner("Indexing…"):
                    try:
                        client.index_text(raw_name.strip(), raw_text)
                        st.success(f"✅ '{raw_name}' indexed!")
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Failed: {exc.detail}")
            else:
                st.warning("Source name and text are both required.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Search Preview
# ═══════════════════════════════════════════════════════════════════════════════
with tab_preview:
    st.subheader("Test Retrieval")

    try:
        all_src = client.list_sources()
        indexed_src = [s for s in all_src if s["status"] == "indexed"]
    except APIError:
        indexed_src = []

    if not indexed_src:
        st.info("Index some data first to test retrieval here.")
    else:
        selected_name = st.selectbox("Select Source", [s["source_name"] for s in indexed_src])
        query = st.text_input("Query", placeholder="e.g. conference venue with catering in city centre")
        n_results = st.slider("Chunks to retrieve", 1, 10, 5)

        if st.button("Search", type="primary") and query:
            src = next((s for s in indexed_src if s["source_name"] == selected_name), None)
            if src:
                with st.spinner("Searching…"):
                    try:
                        result = client.ai_rag(
                            query=query,
                            collection_names=[src["collection_name"]],
                            n_results=n_results,
                        )
                        st.markdown(f"**Answer:** {result['answer']}")
                        st.caption(f"Confidence: `{result['confidence']}`")
                    except APIError as exc:
                        st.error(f"Search failed: {exc.detail}")
