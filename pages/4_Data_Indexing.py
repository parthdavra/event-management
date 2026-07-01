import streamlit as st
from modules.auth import require_auth, show_sidebar
from modules.rag import is_configured, query_collection
from modules.indexing import (
    index_source, index_city_data, get_user_sources, delete_source,
    extract_text_from_pdf, extract_text_from_docx,
)
from modules.api_fetcher import (
    ALL_CATEGORIES, FOURSQUARE_API_KEY, GEOAPIFY_API_KEY,
    fetch_all_city_venues, get_city_coords,
)

st.set_page_config(page_title="Data Indexing — AI Event Manager", page_icon="🗂️", layout="wide")
require_auth()
show_sidebar()

st.title("🗂️ Data Indexing")

if not is_configured():
    st.warning(
        "Azure OpenAI is not configured. Indexing requires the embeddings API — "
        "set `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`."
    )
    st.stop()


# ── Shared helper (defined before tab blocks so it's in scope everywhere) ──────

def render_source_card(src: dict):
    col_info, col_act = st.columns([3, 1])
    with col_info:
        st.markdown(f"**Chunks:** {src['chunk_count']}")
        st.markdown(f"**Status:** {src['status']}")
        if src["indexed_at"]:
            st.markdown(f"**Indexed:** {src['indexed_at'].strftime('%d %b %Y, %H:%M')}")
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
                ok, err = delete_source(src["id"], st.session_state["user_id"])
                if ok:
                    st.session_state.pop(f"confirm_src_{src['id']}", None)
                    st.rerun()
                else:
                    st.error(err)
        with n:
            if st.button("Cancel", key=f"no_src_{src['id']}"):
                st.session_state.pop(f"confirm_src_{src['id']}", None)
                st.rerun()


# ── Tabs ───────────────────────────────────────────────────────────────────────

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
        "Each city gets its **own ChromaDB collection** so the AI assistant can answer "
        "location-specific questions like *'Find a conference room in London'*."
    )

    # API status
    col_osm, col_fsq, col_otm = st.columns(3)
    col_osm.success("✅ OpenStreetMap — always available (free)")
    if FOURSQUARE_API_KEY:
        col_fsq.success("✅ Foursquare — key configured")
    else:
        col_fsq.info("ℹ️ Foursquare — set FOURSQUARE_API_KEY to enable")
    if GEOAPIFY_API_KEY:
        col_otm.success("✅ Geoapify — key configured")
    else:
        col_otm.info("ℹ️ Geoapify — set GEOAPIFY_API_KEY to enable (free at geoapify.com)")

    st.markdown("---")

    # Controls
    col_city, col_radius = st.columns([3, 1])
    with col_city:
        city_input = st.text_input(
            "City *",
            placeholder="e.g. London, New York, Sydney, Mumbai",
        )
    with col_radius:
        radius_km = st.slider("Radius (km)", 1, 20, 5)

    selected_cats = st.multiselect(
        "Venue Categories *",
        options=ALL_CATEGORIES,
        default=["Restaurants & Cafes", "Conference & Event Venues", "Hotels & Accommodation"],
    )

    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        use_fsq = st.toggle("Use Foursquare", value=bool(FOURSQUARE_API_KEY), disabled=not FOURSQUARE_API_KEY)
    with col_t2:
        use_geo_toggle = st.toggle("Use Geoapify", value=bool(GEOAPIFY_API_KEY), disabled=not GEOAPIFY_API_KEY)
    with col_t3:
        max_venues = st.number_input("Max venues", min_value=10, max_value=1000, value=300, step=50)

    fetch_clicked = st.button(
        "🔍 Fetch Venues",
        type="primary",
        disabled=not city_input.strip() or not selected_cats,
    )

    # Fetch step
    if fetch_clicked:
        with st.spinner(f"Locating '{city_input}' and querying APIs…"):
            coords = get_city_coords(city_input.strip())
            if not coords:
                st.error(f"Could not find coordinates for **{city_input}**. Check the spelling.")
            else:
                lat, lon = coords
                st.info(f"📍 **{city_input}** found at ({lat:.4f}, {lon:.4f})")

                venues, source_counts = fetch_all_city_venues(
                    city=city_input.strip(),
                    categories=selected_cats,
                    radius_km=radius_km,
                    use_foursquare=use_fsq,
                    use_geoapify=use_geo_toggle,
                    enrich_details=True,
                    max_venues=int(max_venues),
                    coords=coords,
                )
                st.session_state["fetched_venues"] = venues
                st.session_state["fetched_city"] = city_input.strip()
                st.session_state["fetch_source_counts"] = source_counts

    # Preview & index step
    if st.session_state.get("fetched_venues") is not None:
        venues = st.session_state["fetched_venues"]
        city = st.session_state["fetched_city"]
        counts = st.session_state.get("fetch_source_counts", {})

        if not venues:
            st.warning("No venues found. Try a larger radius or more categories.")
        else:
            st.markdown("---")
            st.markdown(f"### Preview — **{city}** ({len(venues)} unique venues found)")

            # Source breakdown
            if counts:
                src_cols = st.columns(len(counts))
                for col, (src, cnt) in zip(src_cols, counts.items()):
                    col.metric(src, cnt)

            # Category breakdown
            type_counts: dict = {}
            for v in venues:
                t = v.get("type", "Other")
                type_counts[t] = type_counts.get(t, 0) + 1

            with st.expander(f"📊 Venue types ({len(type_counts)} categories)"):
                for vtype, cnt in sorted(type_counts.items(), key=lambda x: -x[1])[:25]:
                    st.markdown(f"- **{vtype}**: {cnt}")

            with st.expander("👀 Sample venues (first 20)"):
                rows = [
                    {
                        "Name": v["name"],
                        "Type": v["type"],
                        "Address": v.get("address") or "—",
                        "Rating": v.get("rating") or "—",
                        "Source": v["source"],
                    }
                    for v in venues[:20]
                ]
                st.dataframe(rows, use_container_width=True)

            st.markdown("---")
            replace_existing = st.checkbox("Replace existing collection for this city", value=True)

            col_idx, col_clr = st.columns([3, 1])
            with col_idx:
                if st.button(
                    f"⚡ Index {len(venues)} venues into **'{city}'** collection",
                    type="primary",
                ):
                    with st.spinner(f"Embedding & indexing {len(venues)} venues…"):
                        src_id, err = index_city_data(
                            user_id=st.session_state["user_id"],
                            city=city,
                            venues=venues,
                            replace_existing=replace_existing,
                        )
                    if src_id:
                        st.success(
                            f"✅ **{city}** indexed! {len(venues)} venues are now searchable "
                            f"by the AI assistant."
                        )
                        st.session_state.pop("fetched_venues", None)
                        st.session_state.pop("fetched_city", None)
                        st.session_state.pop("fetch_source_counts", None)
                        st.rerun()
                    else:
                        st.error(f"Indexing failed: {err}")

            with col_clr:
                if st.button("✖ Clear"):
                    st.session_state.pop("fetched_venues", None)
                    st.session_state.pop("fetched_city", None)
                    st.session_state.pop("fetch_source_counts", None)
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Indexed Sources
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sources:
    sources = get_user_sources(st.session_state["user_id"])

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
                with st.expander(f"{icon} **{src['source_name']}** — {src['source_type'].upper()}"):
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
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            file_bytes = uploaded.read()
            with st.spinner(f"Indexing '{name}'…"):
                try:
                    if ext == "pdf":
                        text, ftype = extract_text_from_pdf(file_bytes), "pdf"
                    elif ext == "docx":
                        text, ftype = extract_text_from_docx(file_bytes), "docx"
                    else:
                        text, ftype = file_bytes.decode("utf-8", errors="ignore"), "txt"
                    src_id, err = index_source(st.session_state["user_id"], name, ftype, text)
                    if src_id:
                        st.success(f"✅ '{name}' indexed!")
                        st.rerun()
                    else:
                        st.error(f"Failed: {err}")
                except Exception as e:
                    st.error(f"Error: {e}")

    else:
        raw_name = st.text_input("Source Name *", placeholder="e.g. Meeting Notes June 2026")
        raw_text = st.text_area("Paste Text *", height=300, placeholder="Paste content here…")
        if st.button("Index Text", type="primary"):
            if raw_name and raw_text:
                with st.spinner("Indexing…"):
                    src_id, err = index_source(
                        st.session_state["user_id"], raw_name.strip(), "raw_text", raw_text
                    )
                if src_id:
                    st.success(f"✅ '{raw_name}' indexed!")
                    st.rerun()
                else:
                    st.error(f"Failed: {err}")
            else:
                st.warning("Source name and text are both required.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Search Preview
# ═══════════════════════════════════════════════════════════════════════════════
with tab_preview:
    st.subheader("Test Retrieval")

    all_src = get_user_sources(st.session_state["user_id"])
    indexed_src = [s for s in all_src if s["status"] == "indexed"]

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
                        docs, metas = query_collection(src["collection_name"], query, n_results)
                        st.markdown(f"**{len(docs)} result(s) for:** *{query}*")
                        for i, (doc, meta) in enumerate(zip(docs, metas)):
                            label = meta.get("venue_name") or meta.get("source") or f"chunk {i + 1}"
                            with st.expander(f"Result {i + 1} — {label}"):
                                st.markdown(doc)
                    except Exception as e:
                        st.error(f"Search failed: {e}")
