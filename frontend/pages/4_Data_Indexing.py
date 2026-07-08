import json

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

POPULAR_EVENT_TYPES = [
    "Awards ceremony", "Away day", "Bar mitzvah", "Birthday party",
    "Car launch", "Christmas Party", "Conference", "Corporate party",
    "Corporate reception", "Exhibition", "Fashion show", "Meeting",
    "Networking", "Office staff party", "Pop Up", "Presentation",
    "Press day", "Private dining", "Private party", "Private screening",
    "Product launch", "Team building", "Training", "Wedding reception", "Workshop",
]

CANVAS_FEATURES = [
    # Venue specs
    "Accommodation", "Air Conditioning", "Breakout rooms", "Cloakroom",
    "Dancefloor", "Dog friendly", "Early access", "Elevator", "Goods lift",
    "Loading bay", "Outside area", "Parking Facilities", "Rain friendly",
    "Ramps", "Separate Entrance", "Smoking area", "Toilets", "Wheelchair",
    "Whiteboards/ flipcharts",
    # Technical
    "AV equipment", "BYO DJ", "Hearing loop", "High Speed Fibre Optic",
    "On-site technician", "PA System", "Screens / Projector", "Sky Sports",
    "Stage", "Video Conferencing", "Video Recording", "Wi-Fi",
    # Catering
    "External caterers", "Can provide Halal", "Can provide Kosher", "Dry Hire",
    "Fridge/Freezer", "Full Catering Kitchen", "Inhouse caterers available",
    "Kitchen Facilities", "Tables & Chairs", "Tableware", "Vegan Friendly",
    "Venue can provide alcohol", "Wet Hire",
    # Allowed
    "18th Birthday", "21st Birthday", "Child friendly", "Loud Music",
    "Open past 12am", "Ticketed Events",
    # Licensing
    "Alcohol License", "BYOB", "Civil ceremony licence", "Full wedding license",
    "Late License", "Tens Available",
]

CANVAS_VENUE_STYLES = [
    "Academic", "Activity Bar", "Arenas", "Auditorium", "Ballroom",
    "Banquet hall", "Bars", "Blank Canvas", "Boardroom", "Boat",
    "Cafe", "Church", "Cinema", "City Mansions", "Co-working space",
    "Community centre", "Conference space", "Country house", "Courtyards",
    "Creative Spaces", "Dance studios", "Dry hire", "Event Space",
    "Exhibition Centres", "Festival", "Function room", "Galleries",
    "Gardens", "Halls", "Historic", "Hotels", "Indoor sporting venues",
    "Industrial Spaces", "Institutional", "Kitchens", "Landmark Buildings",
    "Large Scale", "Livery", "Luxury", "Members Club", "Minimum spend",
    "Modern", "Museums", "Music Venues", "Nightclubs",
    "Outdoor sporting venue", "Penthouse / Apartment", "Private House",
    "Pubs", "Railway arch", "Rehearsal space", "Restaurants", "Riverside",
    "Roof Terrace", "Sports Halls", "Stately Homes", "Supper club",
    "Theatres", "Themed", "Townhouses", "Unique / Unusual",
    "Vacant Spaces", "Warehouses",
]

# slug converters (match Canvas URL slug format)
def _to_canvas_slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower().strip()).strip("-")


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


# ── Shared dietary display constants (used by Nearby Vendors + Catering Planner) ──
_DIETARY_ICONS = {
    "vegan":        ("🌱", "Vegan"),
    "vegetarian":   ("🥗", "Vegetarian"),
    "halal":        ("☪️",  "Halal"),
    "kosher":       ("✡️",  "Kosher"),
    "gluten-free":  ("🌾", "Gluten-Free"),
    "dairy-free":   ("🥛", "Dairy-Free"),
    "nut-free":     ("🥜", "Nut-Free"),
}
_PRICE_LABELS = {"£": "Budget", "££": "Mid-range", "£££": "Premium", "££££": "Luxury"}
_DIETARY_BADGE_MAP = {
    "is_vegan":        ("🌱", "Vegan"),
    "is_vegetarian":   ("🥗", "Vegetarian"),
    "is_halal":        ("☪️",  "Halal"),
    "is_kosher":       ("✡️",  "Kosher"),
    "is_low_carb":     ("⚡", "Low Carb"),
    "is_high_protein": ("💪", "High Protein"),
    "is_pescatarian":  ("🐟", "Pescatarian"),
}
_ALLERGEN_MAP = {
    "allergen_milk":    "🥛 Milk",
    "allergen_nuts":    "🥜 Nuts",
    "allergen_peanuts": "🥜 Peanuts",
    "allergen_cereals": "🌾 Gluten",
    "allergen_eggs":    "🥚 Eggs",
    "allergen_fish":    "🐟 Fish",
}
_DIETARY_OPTIONS = ["vegan", "vegetarian", "halal", "kosher", "non-veg", "gluten-free"]

tab_api, tab_event_type, tab_from_json, tab_feedr, tab_nearby, tab_sources, tab_add, tab_preview, tab_catering = st.tabs([
    "🌍 Fetch from APIs",
    "🎯 By Event Type",
    "📥 From JSON",
    "🍽️ Catering Vendors (Feedr.co)",
    "📍 Nearby Vendors",
    "📋 Indexed Sources",
    "📄 Add Document",
    "🔍 Search Preview",
    "🍱 Catering Planner",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Fetch from APIs
# ═══════════════════════════════════════════════════════════════════════════════
with tab_api:
    st.subheader("Fetch & Index Venue Data by City")
    st.markdown(
        "Search real venue data from public APIs. "
        "Each city gets its **own OpenSearch collection** so the AI can answer location-specific questions."
    )

    col_city, col_radius = st.columns([3, 1])
    with col_city:
        city_input = st.text_input("City *", placeholder="e.g. London, Manchester")
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
            st.markdown(f"### Preview — **{city}** ({len(venues)} venues)")
            tool_badge(_ts, _tn)
            data_source_badges(counts)

            with st.expander("👀 Sample venues (first 20)"):
                rows = [
                    {
                        "Name": v["name"],
                        "Type": v.get("type") or "—",
                        "Address": v.get("address") or "—",
                        "Price": v.get("price_range") or v.get("price_per_day") or "—",
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
                        client.index_city(city=city, venues=venues, replace_existing=replace_existing)
                        st.success(f"✅ **{city}** indexed! {len(venues)} venues are now searchable.")
                        st.session_state.pop("fetched_venues", None)
                        st.session_state.pop("fetched_city", None)
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Indexing failed: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — By Event Type
# ═══════════════════════════════════════════════════════════════════════════════
with tab_event_type:
    st.subheader("Index Venues by Event Type")

    # ── BULK SECTION (primary UI) ──────────────────────────────────────────────
    st.markdown("### ⚡ Bulk Index — All Events · All Venues · All Cities · All Facilities")
    st.markdown(
        "One click: fetches venues for every selected city, "
        "creates **one collection per event type**, stores **2 chunks per venue** "
        "(rich semantic text + raw JSON). All Canvas features/facilities are included automatically."
    )

    bulk_c1, bulk_c2 = st.columns([2, 1])
    with bulk_c1:
        bulk_cities = st.multiselect(
            "Cities *",
            options=["London", "Manchester", "Birmingham", "Edinburgh", "Bristol", "Leeds", "Glasgow"],
            default=["London"],
            key="bulk_cities",
        )
        custom_city = st.text_input("Add custom city", placeholder="e.g. Liverpool", key="bulk_custom_city")
        if custom_city.strip() and custom_city.strip().title() not in bulk_cities:
            bulk_cities = bulk_cities + [custom_city.strip().title()]
    with bulk_c2:
        bulk_radius = st.slider("Search radius (km)", 1, 20, 5, key="bulk_radius")
        bulk_max_venues = st.number_input("Max venues per city", 50, 500, 300, step=50, key="bulk_max")

    bulk_event_types = st.multiselect(
        "Event Types (all selected by default)",
        options=POPULAR_EVENT_TYPES,
        default=POPULAR_EVENT_TYPES,
        key="bulk_event_types",
    )

    bulk_cats = st.multiselect(
        "Venue Categories",
        options=ALL_CATEGORIES,
        default=["Conference & Event Venues", "Restaurants & Cafes", "Hotels & Accommodation",
                 "Bars & Nightlife", "Arts & Entertainment"],
        key="bulk_cats",
    )

    bulk_replace = st.checkbox("Replace existing collections", value=True, key="bulk_replace")

    uid = st.session_state.get("user_id", 0)
    est_chunks = len(bulk_event_types) * int(bulk_max_venues) * len(bulk_cities) * 2
    st.info(
        f"**Estimated scope:** {len(bulk_cities)} cities × {len(bulk_event_types)} event types "
        f"→ up to **{len(bulk_event_types)} collections**, "
        f"~{int(bulk_max_venues) * len(bulk_cities):,} venues (deduplicated), "
        f"~{est_chunks:,} chunks."
    )

    job_running = "bulk_job_id" in st.session_state

    bulk_btn = st.button(
        f"🚀 Index All: {len(bulk_cities)} cities × {len(bulk_event_types)} event types",
        type="primary",
        disabled=job_running or not bulk_cities or not bulk_event_types or not bulk_cats,
        key="bulk_index_btn",
    )

    if bulk_btn and bulk_cities and bulk_event_types:
        try:
            resp = client.bulk_index_event_types(
                cities=bulk_cities,
                event_types=bulk_event_types,
                categories=bulk_cats,
                radius_km=bulk_radius,
                max_venues_per_city=int(bulk_max_venues),
                replace_existing=bulk_replace,
            )
            st.session_state["bulk_job_id"] = resp["job_id"]
            st.session_state.pop("bulk_stats", None)
            st.rerun()
        except APIError as exc:
            st.error(f"Failed to start bulk indexing: {exc.detail}")

    # ── Polling loop ───────────────────────────────────────────────────────────
    if "bulk_job_id" in st.session_state:
        import time as _time
        job_id = st.session_state["bulk_job_id"]
        try:
            job = client.get_bulk_index_job(job_id)
        except APIError as exc:
            st.error(f"Failed to poll job: {exc.detail}")
            del st.session_state["bulk_job_id"]
            job = None

        if job:
            if job["status"] == "running":
                st.info("⏳ Bulk indexing in progress — this may take several minutes. Page refreshes automatically.")
                _time.sleep(5)
                st.rerun()
            elif job["status"] == "done":
                st.session_state["bulk_stats"] = job["result"]
                del st.session_state["bulk_job_id"]
                st.rerun()
            elif job["status"] == "error":
                st.error(f"Bulk indexing failed: {job.get('error', 'unknown error')}")
                del st.session_state["bulk_job_id"]

    # ── Results display ────────────────────────────────────────────────────────
    bulk_stats = st.session_state.get("bulk_stats")
    if bulk_stats:
        st.markdown("---")

        # Top-level KPI metrics
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Collections created", bulk_stats.get("total_collections", 0))
        r2.metric("Total unique venues", bulk_stats.get("total_venues", 0))
        r3.metric("Total chunks indexed", bulk_stats.get("total_chunks", 0))
        city_fetched = bulk_stats.get("cities_fetched") or []
        total_fetched = sum(c.get("venues", 0) for c in city_fetched)
        r4.metric("Venues fetched (pre-dedup)", total_fetched)

        # Cities summary
        if city_fetched:
            st.markdown("**Cities fetched:**")
            city_cols = st.columns(min(len(city_fetched), 4))
            for col, c in zip(city_cols, city_fetched):
                col.metric(c["city"].title(), f"{c['venues']:,} venues")

        # Errors
        errors = bulk_stats.get("errors") or []
        if errors:
            with st.expander(f"⚠️ {len(errors)} errors", expanded=True):
                for e in errors:
                    st.error(e)

        # Per-collection results table
        collections = bulk_stats.get("collections") or []
        if collections:
            st.markdown("#### Collections indexed")
            rows = []
            for c in collections:
                status_icon = "✅" if c.get("status") == "indexed" else "❌"
                rows.append({
                    "Status": status_icon,
                    "Event Type": c.get("event_type", ""),
                    "Collection": c.get("collection_name", ""),
                    "Venues": c.get("total_venues", 0),
                    "Matched": c.get("matched_venues", 0),
                    "Chunks": c.get("chunks", 0),
                    "Error": c.get("error", "") or "",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # Download summary
            st.download_button(
                "⬇️ Download index summary JSON",
                data=json.dumps(bulk_stats, indent=2, ensure_ascii=False),
                file_name="bulk_index_summary.json",
                mime="application/json",
            )

        if st.button("🗑️ Clear results", key="clear_bulk_stats"):
            st.session_state.pop("bulk_stats", None)
            st.rerun()

    st.markdown("---")

    # ── SINGLE EVENT TYPE section (secondary) ──────────────────────────────────
    with st.expander("🔍 Search & preview a single event type (optional)", expanded=False):
        et_c1, et_c2, et_c3 = st.columns([2, 2, 1])
        with et_c1:
            selected_event_type = st.selectbox(
                "Event Type",
                options=POPULAR_EVENT_TYPES,
                index=6,
                key="single_et_select",
            )
        with et_c2:
            et_city = st.text_input("City", value="London", key="et_city")
        with et_c3:
            et_radius = st.slider("Radius (km)", 1, 20, 5, key="et_radius")

        with st.expander("🎛️ Canvas Filters", expanded=False):
            feat_col, style_col = st.columns(2)
            with feat_col:
                sel_features = st.multiselect("Features", options=CANVAS_FEATURES, default=[], key="et_features")
            with style_col:
                sel_styles = st.multiselect("Venue styles", options=CANVAS_VENUE_STYLES, default=[], key="et_styles")
            if et_city.strip():
                city_slug = _to_canvas_slug(et_city.strip())
                et_slug_url = _to_canvas_slug(selected_event_type)
                params = []
                if sel_features:
                    params.append("features=" + "%2C".join(_to_canvas_slug(f) for f in sel_features))
                if sel_styles:
                    params.append("venue_types=" + "%2C".join(_to_canvas_slug(s) for s in sel_styles))
                if et_slug_url:
                    params.append(f"event_type={et_slug_url}")
                canvas_url = f"https://www.canvas-events.co.uk/hire-venue-{city_slug}" + ("?" + "&".join(params) if params else "")
                st.caption(f"Canvas URL: `{canvas_url}`")

        et_cats = st.multiselect(
            "Categories", options=ALL_CATEGORIES, default=["Conference & Event Venues"], key="et_cats"
        )

        if st.button(f"🔍 Fetch for {selected_event_type} / {et_city or '…'}", key="et_fetch_btn",
                     disabled=not et_city.strip()):
            with st.spinner(f"Fetching '{selected_event_type}' venues in {et_city}…"):
                try:
                    result = client.search_venues(
                        city=et_city.strip(), categories=et_cats, radius_km=et_radius,
                        use_foursquare=True, use_geoapify=True, enrich_details=True,
                        max_venues=300, event_type=selected_event_type,
                    )
                    st.session_state["et_venues"] = result["venues"]
                    st.session_state["et_city_used"] = et_city.strip()
                    st.session_state["et_event_type_used"] = selected_event_type
                    st.session_state["et_source_counts"] = result["source_counts"]
                except APIError as exc:
                    st.error(f"Error: {exc.detail}")

        if st.session_state.get("et_venues"):
            et_venues = st.session_state["et_venues"]
            et_city_used = st.session_state.get("et_city_used", "")
            et_et_used = st.session_state.get("et_event_type_used", "")

            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Venues", len(et_venues))
            sc2.metric("Event type match", sum(1 for v in et_venues if v.get("event_type_match")))
            sc3.metric("Canvas Events", sum(1 for v in et_venues if v.get("source") == "Canvas Events"))

            with st.expander("👀 Preview (first 20)"):
                rows = [{
                    "Name": v.get("name", "—"), "Source": v.get("source", "—"),
                    "Match": "✅" if v.get("event_type_match") else "—",
                    "Capacity": v.get("capacity") or "—",
                    "Price": (v.get("canvas_price_guide") or {}).get("from_price") or v.get("price_range") or "—",
                } for v in et_venues[:20]]
                st.dataframe(rows, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇️ Download JSON",
                data=json.dumps(et_venues, indent=2, ensure_ascii=False),
                file_name=f"{_to_canvas_slug(et_et_used)}_{_to_canvas_slug(et_city_used)}_venues.json",
                mime="application/json",
            )

            et_slug_col = _to_canvas_slug(et_et_used)
            col_name = f"evt_u{uid}_{et_slug_col[:20]}"
            if st.button(f"⚡ Index {len(et_venues)} venues → `{col_name}`", key="et_index_btn"):
                with st.spinner("Indexing…"):
                    try:
                        client.index_event_type_venues(
                            event_type=et_et_used, city=et_city_used, venues=et_venues,
                        )
                        st.success(f"✅ Indexed into `{col_name}`")
                        st.session_state.pop("et_venues", None)
                        st.rerun()
                    except APIError as exc:
                        st.error(f"Failed: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — From JSON
# ═══════════════════════════════════════════════════════════════════════════════
with tab_from_json:
    st.subheader("Index from Raw JSON")
    st.markdown(
        "Paste venue JSON (single venue `{}` or array `[…]`). "
        "The system reads the **`website`** field of each Canvas Events venue, "
        "fetches the full venue detail page (price guide, capacity, spaces, features), "
        "then stores everything as searchable chunks."
    )

    fj_raw = st.text_area(
        "Paste venue JSON *",
        height=260,
        placeholder='[{"name": "KOKO", "website": "https://www.canvas-events.co.uk/venues/8/koko", ...}]',
        key="fj_raw_input",
    )

    # Parse JSON on the fly for preview
    fj_venues: list[dict] = []
    fj_parse_error = ""
    if fj_raw.strip():
        try:
            parsed = json.loads(fj_raw.strip())
            fj_venues = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError as exc:
            fj_parse_error = str(exc)

    if fj_parse_error:
        st.error(f"JSON parse error: {fj_parse_error}")
    elif fj_venues:
        canvas_count = sum(1 for v in fj_venues if "canvas-events.co.uk" in (v.get("website") or ""))
        other_count = len(fj_venues) - canvas_count

        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Venues in JSON", len(fj_venues))
        pc2.metric("Canvas Events venues", canvas_count, help="Will be enriched from detail page")
        pc3.metric("Other venues", other_count, help="Indexed as-is")

        # Preview table
        with st.expander("👀 Parsed venues", expanded=len(fj_venues) <= 5):
            rows = [
                {
                    "Name": v.get("name", "—"),
                    "Source": v.get("source", "—"),
                    "Website": (v.get("website") or "")[:60] or "—",
                    "Capacity": v.get("capacity", "—"),
                    "Will Enrich": "✅" if "canvas-events.co.uk" in (v.get("website") or "") else "—",
                }
                for v in fj_venues
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("---")

    fj_c1, fj_c2 = st.columns(2)
    with fj_c1:
        fj_event_type = st.selectbox(
            "Event Type (for collection name)",
            options=["general"] + POPULAR_EVENT_TYPES,
            key="fj_event_type",
        )
    with fj_c2:
        fj_city = st.text_input("City", value="London", key="fj_city")

    fj_replace = st.checkbox("Replace existing collection for this event type", value=True, key="fj_replace")

    uid = st.session_state.get("user_id", 0)
    et_slug_preview = _to_canvas_slug(fj_event_type)
    col_preview = f"evt_u{uid}_{et_slug_preview[:20]}"
    if fj_venues:
        st.caption(f"Target collection: `{col_preview}` · {len(fj_venues) * 2} chunks ({len(fj_venues)} rich + {len(fj_venues)} raw JSON)")

    fj_btn = st.button(
        "🚀 Enrich from Canvas + Index as chunks",
        type="primary",
        disabled=not fj_venues,
        key="fj_run_btn",
    )

    if fj_btn and fj_venues:
        with st.spinner(f"Enriching {canvas_count} Canvas venue(s) and indexing {len(fj_venues)} total…"):
            try:
                result = client.index_from_json(
                    venues=fj_venues,
                    event_type=fj_event_type,
                    city=fj_city.strip(),
                    replace_existing=fj_replace,
                )
                st.success(
                    f"✅ **{result['total_venues']} venues indexed** into `{result['collection_name']}` — "
                    f"{result['canvas_enriched']} enriched from Canvas Events."
                )

                # Show per-venue enrichment results
                enrichment_results = result.get("enrichment_results", [])
                if enrichment_results:
                    st.markdown("#### Enrichment Results")
                    for r in enrichment_results:
                        name = r.get("name", "Unknown")
                        if r.get("error"):
                            st.error(f"**{name}** — enrichment failed: {r['error']}")
                        elif r.get("enriched"):
                            filled = r.get("fields_filled", [])
                            icons = []
                            if r.get("has_price_guide"):
                                icons.append("💷 Price guide")
                            if r.get("has_capacity"):
                                icons.append("👥 Capacity")
                            if r.get("has_spaces"):
                                icons.append("🏢 Spaces")
                            if r.get("has_features"):
                                icons.append("✅ Features")
                            if r.get("has_perfect_for"):
                                icons.append("🎯 Perfect for")
                            details = " · ".join(icons) if icons else "basic fields"
                            st.success(f"**{name}** — enriched: {details}")
                            if filled:
                                st.caption(f"Fields filled: {', '.join(filled)}")
                        else:
                            note = r.get("note", "")
                            st.info(f"**{name}** — indexed as-is ({note})")

                # Show full enriched data in expandable section
                st.markdown("---")
                with st.expander("🔍 View indexed data (raw JSON per venue)", expanded=False):
                    # Re-enrich locally for display only — call preview not available,
                    # so display what the user pasted + a note about enrichment
                    st.caption("Showing original input JSON. The enriched version (with Canvas data) is stored in the collection.")
                    st.json(fj_venues[:3] if len(fj_venues) > 3 else fj_venues)
                    if len(fj_venues) > 3:
                        st.caption(f"… and {len(fj_venues) - 3} more venues.")

            except APIError as exc:
                st.error(f"Failed: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Catering Vendors (Feedr.co)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_feedr:
    st.subheader("Index Catering Vendors from Feedr.co")
    st.markdown(
        "Fetches catering vendors from [feedr.co](https://feedr.co/en-gb/office-catering/vendors) "
        "via the CaterDesk GraphQL API — no Playwright required. "
        "Data is indexed into a dedicated OpenSearch collection (`feedr_u{user_id}_{city}`) "
        "as **2 chunks per vendor**: rich semantic text + raw JSON for exact retrieval.\n\n"
        "Feedr.co covers 200+ office catering vendors in London and other UK cities, "
        "with cuisine tags, dietary flags, ratings, and per-location addresses."
    )

    fd_city = st.text_input(
        "City *",
        value="London",
        placeholder="e.g. London, Manchester",
        key="feedr_city",
    )

    with st.expander("🗺️ Override coordinates (optional)", expanded=False):
        st.caption(
            "Leave blank to let the backend geocode the city automatically. "
            "Provide lat/lon if the city name is ambiguous."
        )
        oc1, oc2 = st.columns(2)
        with oc1:
            fd_lat = st.number_input("Latitude", value=0.0, format="%.6f", key="feedr_lat")
        with oc2:
            fd_lon = st.number_input("Longitude", value=0.0, format="%.6f", key="feedr_lon")
        use_override = st.checkbox("Use these coordinates", value=False, key="feedr_use_override")

    fd_replace = st.checkbox("Replace existing Feedr collection for this city", value=True, key="feedr_replace")

    feedr_job_running = "feedr_job_id" in st.session_state

    fd_btn = st.button(
        f"🔍 Scrape & Index Feedr.co vendors — {fd_city or '…'}",
        type="primary",
        disabled=feedr_job_running or not fd_city.strip(),
        key="feedr_run_btn",
    )

    if fd_btn and fd_city.strip():
        lat_arg = fd_lat if use_override and fd_lat != 0.0 else None
        lon_arg = fd_lon if use_override and fd_lon != 0.0 else None
        try:
            resp = client.scrape_and_index_feedr(
                city=fd_city.strip(),
                lat=lat_arg,
                lon=lon_arg,
                replace_existing=fd_replace,
            )
            st.session_state["feedr_job_id"] = resp["job_id"]
            st.session_state.pop("feedr_result", None)
            st.rerun()
        except APIError as exc:
            st.error(f"Failed to start Feedr scrape: {exc.detail}")

    # ── Polling loop ───────────────────────────────────────────────────────────
    if "feedr_job_id" in st.session_state:
        import time as _time
        feedr_jid = st.session_state["feedr_job_id"]
        try:
            feedr_job = client.get_feedr_job(feedr_jid)
        except APIError as exc:
            st.error(f"Failed to poll Feedr job: {exc.detail}")
            del st.session_state["feedr_job_id"]
            feedr_job = None

        if feedr_job:
            if feedr_job["status"] == "running":
                st.info("⏳ Scraping feedr.co and indexing vendors… (may take 30–90 seconds)")
                _time.sleep(4)
                st.rerun()
            elif feedr_job["status"] == "done":
                st.session_state["feedr_result"] = feedr_job["result"]
                del st.session_state["feedr_job_id"]
                st.rerun()
            elif feedr_job["status"] == "error":
                st.error(f"Feedr scrape failed: {feedr_job.get('error', 'unknown error')}")
                del st.session_state["feedr_job_id"]

    # ── Results display ────────────────────────────────────────────────────────
    feedr_result = st.session_state.get("feedr_result")
    if feedr_result:
        st.markdown("---")

        if feedr_result.get("warning"):
            st.warning(feedr_result["warning"])
        else:
            fr1, fr2, fr3 = st.columns(3)
            fr1.metric("Vendors scraped", feedr_result.get("vendors", 0))
            fr2.metric("Chunks indexed", feedr_result.get("chunks", 0))
            fr3.metric("Collection", feedr_result.get("collection_name", "—"))

            sample = feedr_result.get("sample", [])
            if sample:
                st.markdown("**Sample vendors found:**")
                for n in sample:
                    st.markdown(f"- {n}")

            city_done = feedr_result.get("city", "")
            st.success(
                f"✅ **{feedr_result.get('vendors', 0)} Feedr.co vendors** indexed for **{city_done}**. "
                f"Collection `{feedr_result.get('collection_name', '')}` is now searchable via RAG."
            )

        if st.button("🗑️ Clear results", key="feedr_clear_results"):
            st.session_state.pop("feedr_result", None)
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Nearby Vendors
# ═══════════════════════════════════════════════════════════════════════════════
with tab_nearby:
    st.subheader("Find Nearby Catering Vendors")
    st.markdown(
        "Enter coordinates (or auto-geocode a city) to find the closest "
        "catering vendors from Feedr.co, sorted by distance. "
        "Results come directly from the CaterDesk GraphQL API — no indexing needed."
    )

    nb_method = st.radio("Locate by", ["City name (auto-geocode)", "Lat / Lon (manual)"], horizontal=True, key="nb_method")

    if nb_method == "City name (auto-geocode)":
        nb_city = st.text_input("City *", value="London", placeholder="e.g. London, Manchester", key="nb_city")
        nb_lat_input, nb_lon_input = 0.0, 0.0
    else:
        nb_city = ""
        nb_c1, nb_c2 = st.columns(2)
        with nb_c1:
            nb_lat_input = st.number_input("Latitude *", value=51.5074, format="%.6f", key="nb_lat")
        with nb_c2:
            nb_lon_input = st.number_input("Longitude *", value=-0.1278, format="%.6f", key="nb_lon")

    nb_max = st.slider("Max results", min_value=5, max_value=50, value=20, step=5, key="nb_max")

    nb_btn = st.button("🔍 Find Nearby Vendors", type="primary", key="nb_search_btn")

    if nb_btn:
        # Geocode city if needed
        lat_to_use, lon_to_use = nb_lat_input, nb_lon_input
        if nb_method == "City name (auto-geocode)":
            if not nb_city.strip():
                st.warning("Please enter a city name.")
                st_stop = True
            else:
                with st.spinner(f"Geocoding '{nb_city}'…"):
                    try:
                        coords_resp = client.mcp_geocode_city(nb_city.strip())
                        lat_to_use = float(coords_resp.get("lat") or 0.0)
                        lon_to_use = float(coords_resp.get("lon") or 0.0)
                    except Exception:
                        lat_to_use, lon_to_use = 0.0, 0.0

                    if not lat_to_use and not lon_to_use:
                        st.error(f"Could not geocode '{nb_city}'. Try entering lat/lon manually.")
                        lat_to_use = None

        if lat_to_use is not None and (lat_to_use != 0.0 or lon_to_use != 0.0):
            with st.spinner(f"Fetching up to {nb_max} vendors near ({lat_to_use:.4f}, {lon_to_use:.4f})…"):
                try:
                    result = client.get_nearby_vendors(lat=lat_to_use, lon=lon_to_use, max_results=nb_max)
                    st.session_state["nearby_result"] = result
                    st.session_state["nearby_coords"] = (lat_to_use, lon_to_use)
                except APIError as exc:
                    st.error(f"Failed: {exc.detail}")

    # ── Results ────────────────────────────────────────────────────────────────
    nearby_result = st.session_state.get("nearby_result")
    if nearby_result:
        vendors_list = nearby_result.get("vendors", [])
        coords_used = st.session_state.get("nearby_coords", (0, 0))

        st.markdown("---")
        nb_r1, nb_r2 = st.columns(2)
        nb_r1.metric("Vendors found", len(vendors_list))
        nb_r2.metric("Search point", f"{coords_used[0]:.4f}, {coords_used[1]:.4f}")

        if not vendors_list:
            st.warning("No vendors found near these coordinates.")
        else:
            # Build display table
            rows = []
            for v in vendors_list:
                dist = v.get("distance_km")
                dist_str = f"{dist:.2f} km" if dist is not None else "—"
                price = ""
                if v.get("price_per_head"):
                    price = f"£{v['price_per_head']:.2f}/head"
                elif v.get("price_range"):
                    price = v["price_range"]
                rating = v.get("rating")
                rating_str = f"{rating:.1f} ⭐" if rating else "—"
                rows.append({
                    "Name": v.get("name", "—"),
                    "Distance": dist_str,
                    "Cuisine": (v.get("cuisine") or "").title() or "—",
                    "Price": price or "—",
                    "Rating": rating_str,
                    "Address": (v.get("address") or "")[:50] or "—",
                    "Tags": ", ".join((v.get("tags") or [])[:3]) or "—",
                })

            st.dataframe(rows, use_container_width=True, hide_index=True)

            # Detailed vendor cards

            with st.expander("📋 Full vendor details", expanded=False):
                for v in vendors_list:
                    dist = v.get("distance_km")
                    dist_str = f"{dist:.2f} km" if dist is not None else "—"

                    # Price string
                    if v.get("price_per_head"):
                        price_str = f"£{v['price_per_head']:.0f} per head"
                    elif v.get("price_range"):
                        label = _PRICE_LABELS.get(v["price_range"], "")
                        price_str = f"{v['price_range']}  {label}".strip()
                    else:
                        price_str = "Price on request"

                    # Dietary: combine specializations + any dietary-keyword tags
                    specs = list(v.get("specializations") or [])
                    tag_lower = [t.lower() for t in (v.get("tags") or [])]
                    for kw in _DIETARY_ICONS:
                        if kw not in specs and (kw in tag_lower or kw.replace("-", " ") in tag_lower):
                            specs.append(kw)

                    # Rating stars (★☆)
                    rating = v.get("rating")
                    if rating:
                        filled = min(int(round(rating)), 5)
                        stars = "★" * filled + "☆" * (5 - filled)
                        rating_str = f"{stars} {rating:.1f} ({v.get('total_ratings') or 0} reviews)"
                    else:
                        rating_str = ""

                    # Card layout: text left, image right
                    col_l, col_r = st.columns([4, 1])

                    with col_r:
                        if v.get("logo"):
                            st.image(v["logo"], use_container_width=True)

                    with col_l:
                        # Title + distance badge
                        st.markdown(f"### {v.get('name', '—')}  `{dist_str}`")

                        # Address
                        if v.get("address"):
                            st.markdown(f"📍 {v['address']}")

                        # Price · Cuisine · Rating in one row
                        info_parts = []
                        info_parts.append(f"💷 **{price_str}**")
                        if v.get("cuisine"):
                            info_parts.append(f"🍴 {v['cuisine'].title()}")
                        if rating_str:
                            info_parts.append(rating_str)
                        st.markdown("  ·  ".join(info_parts))

                        # Dietary badges
                        if specs:
                            badge_parts = []
                            for s in specs:
                                icon, label = _DIETARY_ICONS.get(s, ("", s.title()))
                                badge_parts.append(f"{icon} {label}")
                            st.markdown(" ".join(f"`{b}`" for b in badge_parts))
                        else:
                            # No explicit dietary flags → likely contains meat
                            meat_tags = {"chicken", "beef", "lamb", "pork", "fish", "seafood", "meat", "bbq"}
                            if meat_tags & set(tag_lower):
                                st.markdown("`🥩 Contains Meat`")

                        # Description
                        if v.get("description"):
                            st.markdown(f"*{v['description'][:250]}*")

                        # Tags as chips (colour-coding: dietary=green, meal=blue, rest=default)
                        if v.get("tags"):
                            meal_tags = {"lunch", "dinner", "breakfast", "brunch", "snacks"}
                            diet_kws  = set(_DIETARY_ICONS.keys())
                            tag_chips = []
                            for t in v["tags"][:12]:
                                tl = t.lower()
                                if tl in diet_kws or tl.replace("-", " ") in diet_kws:
                                    tag_chips.append(f"🟢 `{t}`")
                                elif tl in meal_tags:
                                    tag_chips.append(f"🔵 `{t}`")
                                else:
                                    tag_chips.append(f"`{t}`")
                            st.markdown("  ".join(tag_chips))

                        # Multiple locations
                        locs = v.get("all_locations") or []
                        if len(locs) > 1:
                            loc_strs = [
                                f"{loc.get('city', '')} {loc.get('postcode', '')}".strip()
                                for loc in locs[:5]
                            ]
                            st.caption(f"🗺️ {len(locs)} delivery locations: " + " · ".join(loc_strs))

                        # CTA buttons
                        btn_col1, btn_col2 = st.columns(2)
                        with btn_col1:
                            permalink = v.get("permalink") or ""
                            menu_key = f"menu_{permalink}"
                            if permalink and st.button("📋 Load Full Menu", key=f"load_{permalink}_{v.get('feedr_id','')}"):
                                with st.spinner("Fetching full menu from Feedr.co…"):
                                    try:
                                        detail = client.get_vendor_detail(permalink)
                                        st.session_state[menu_key] = detail
                                    except Exception as _ex:
                                        st.error(f"Failed to load menu: {_ex}")
                        with btn_col2:
                            if v.get("website"):
                                st.link_button("🔗 Open on Feedr.co", v["website"])

                    # ── Inline full menu (shown below the card, full width) ───
                    permalink = v.get("permalink") or ""
                    menu_key = f"menu_{permalink}"
                    detail = st.session_state.get(menu_key)
                    if detail:
                        with st.expander(
                            f"📋 Full Menu — {detail.get('name')}  ({detail.get('menu_item_count',0)} items)",
                            expanded=True,
                        ):
                            # Vendor gallery images
                            gallery = detail.get("images") or []
                            if gallery:
                                img_cols = st.columns(min(len(gallery), 3))
                                for ic, img_url in enumerate(gallery[:3]):
                                    img_cols[ic].image(img_url, use_container_width=True)

                            # Quote / tagline
                            if detail.get("quote"):
                                st.markdown(f"> *{detail['quote']}*")
                            if detail.get("description"):
                                st.markdown(detail["description"])

                            # Tips (dietary overview) — collapsible
                            if detail.get("tips"):
                                with st.expander("ℹ️ Dietary & Allergen Info"):
                                    st.text(detail["tips"][:600])

                            st.divider()

                            # Menu sections grouped by meal tag
                            grouped = detail.get("menu_grouped") or {}
                            if not grouped:
                                st.info("No menu items available.")
                            else:
                                for section_name, section_items in grouped.items():
                                    st.markdown(f"#### {section_name}  ({len(section_items)} items)")
                                    # Show items in a 2-column grid
                                    for idx in range(0, len(section_items), 2):
                                        ic1, ic2 = st.columns(2)
                                        for col_ref, item in zip([ic1, ic2], section_items[idx:idx+2]):
                                            with col_ref:
                                                # Item image + name in a mini card
                                                img_c, txt_c = col_ref.columns([1, 3])
                                                if item.get("image"):
                                                    img_c.image(item["image"], width=72)
                                                with txt_c:
                                                    # Name + price
                                                    price_str = f"£{item['price_gbp']:.2f}" if item.get("price_gbp") else "—"
                                                    hot_icon = "🔥 " if item.get("is_hot") else ""
                                                    st.markdown(f"**{hot_icon}{item['name']}**  `{price_str}`")
                                                    # Dietary badges
                                                    badges = [v2 for k2, v2 in _DIETARY_BADGE_MAP.items() if item.get(k2)]
                                                    if badges:
                                                        st.markdown(" ".join(f"`{icon} {lbl}`" for icon, lbl in badges))
                                                    # Allergens
                                                    allergens = [lbl for k2, lbl in _ALLERGEN_MAP.items() if item.get(k2)]
                                                    if allergens:
                                                        st.caption("⚠️ " + " · ".join(allergens))
                                                    # Calories
                                                    if item.get("kcal"):
                                                        st.caption(f"🔥 {int(item['kcal'])} kcal")
                                                    # Description (truncated)
                                                    if item.get("description"):
                                                        st.caption(item["description"][:120])
                                    st.markdown("---")

                            # Clear button
                            if st.button("✕ Close menu", key=f"close_{permalink}"):
                                st.session_state.pop(menu_key, None)
                                st.rerun()

                    st.divider()

        if st.button("🗑️ Clear results", key="nb_clear"):
            st.session_state.pop("nearby_result", None)
            st.session_state.pop("nearby_coords", None)
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Indexed Sources
# ═══════════════════════════════════════════════════════════════════════════════
with tab_sources:
    try:
        sources = client.list_sources()
    except APIError:
        sources = []

    if not sources:
        st.info("No indexed sources yet. Use 'Fetch from APIs', 'By Event Type', or 'Add Document'.")
    else:
        total_chunks = sum(s["chunk_count"] for s in sources)
        indexed_ok = sum(1 for s in sources if s["status"] == "indexed")
        city_cnt = sum(1 for s in sources if s["source_type"] == "city_api")
        evt_cnt = sum(1 for s in sources if s["source_type"] == "event_type")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Sources", len(sources))
        c2.metric("Indexed", indexed_ok)
        c3.metric("Total Chunks", total_chunks)
        c4.metric("City Collections", city_cnt)
        c5.metric("Event-Type Collections", evt_cnt)
        st.markdown("---")

        city_sources = [s for s in sources if s["source_type"] == "city_api"]
        evt_sources  = [s for s in sources if s["source_type"] == "event_type"]
        doc_sources  = [s for s in sources if s["source_type"] not in ("city_api", "event_type")]

        if evt_sources:
            st.markdown("#### 🎯 Event-Type Collections")
            for src in evt_sources:
                icon = {"indexed": "✅", "failed": "❌", "pending": "⏳"}.get(src["status"], "❓")
                label = src["source_name"].replace(" — ", " | ")
                with st.expander(f"{icon} **{label}** — {src['chunk_count']} chunks"):
                    render_source_card(src)

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
# TAB 6 — Add Document
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
# TAB 7 — Search Preview
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


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 9 — Catering Planner
# ═══════════════════════════════════════════════════════════════════════════════
with tab_catering:
    import json as _json_cp

    st.subheader("🍱 Catering Requirements Planner")
    st.markdown(
        "Specify your food requirements by dietary group (e.g. 50 vegan, 100 halal, 50 vegetarian) "
        "and find Feedr.co caterers that match — with estimated costs per group."
    )

    # ── Section 0: Load from event ────────────────────────────────────────────
    try:
        _cp_all_events = client.list_events()
        _cp_events_with_catering = [e for e in _cp_all_events if e.get("catering_json")]
    except Exception:
        _cp_all_events = []
        _cp_events_with_catering = []

    if _cp_events_with_catering:
        with st.expander("📅 Load catering requirements from a saved event", expanded=False):
            _cp_ev_opts = {
                f"{e['title']} ({e['date_time'][:10]})": e
                for e in _cp_events_with_catering
            }
            _cp_sel_label = st.selectbox("Select event", list(_cp_ev_opts.keys()), key="cp_event_select")
            _cp_sel_ev = _cp_ev_opts[_cp_sel_label]

            try:
                _cp_preview = _json_cp.loads(_cp_sel_ev["catering_json"])
                _prev_groups = _cp_preview.get("groups") or []
                if _prev_groups:
                    rows = [{"Group": g.get("label",""), "Count": g.get("count",""), "Dietary": g.get("dietary_type","")} for g in _prev_groups]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                pc1, pc2 = st.columns(2)
                if _cp_preview.get("budget"):
                    pc1.metric("Budget", f"£{_cp_preview['budget']:,.0f}" if isinstance(_cp_preview["budget"], (int,float)) else _cp_preview["budget"])
                if _cp_preview.get("location"):
                    pc2.metric("Location", _cp_preview["location"])
            except Exception:
                _cp_preview = {}

            if st.button("📥 Load into Catering Planner", type="primary", key="cp_load_from_event"):
                try:
                    data = _cp_preview if _cp_preview else _json_cp.loads(_cp_sel_ev["catering_json"])
                    if data.get("groups"):
                        st.session_state["cp_groups"] = [
                            {"label": g["label"], "count": int(g["count"]), "dietary_type": g["dietary_type"]}
                            for g in data["groups"]
                        ]
                    if data.get("budget"):
                        st.session_state["cp_budget_val"] = float(data["budget"])
                    if data.get("location"):
                        st.session_state["cp_city_input"] = data["location"]
                        st.session_state["cp_locate"] = "City name"
                    st.success(f"✅ Loaded catering requirements from **{_cp_sel_ev['title']}**.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to load: {exc}")
    else:
        st.info(
            "💡 **Tip:** Go to **Events → My Events** and click **🍱 Add Catering Requirements** to attach "
            "catering briefs to events. They'll appear here for quick loading."
        )

    # ── Section 1: Input mode ─────────────────────────────────────────────────
    cp_method = st.radio(
        "How would you like to enter requirements?",
        ["Manual Entry", "Upload File (PDF / DOCX / TXT)"],
        horizontal=True,
        key="cp_method",
    )

    if cp_method == "Upload File (PDF / DOCX / TXT)":
        cp_file = st.file_uploader(
            "Upload a catering brief or requirements document",
            type=["pdf", "docx", "txt"],
            key="cp_upload",
        )
        if cp_file and st.button("📄 Parse Requirements", key="cp_parse"):
            with st.spinner("Extracting requirements with AI…"):
                try:
                    parsed = client.parse_catering_file(cp_file.read(), cp_file.name)
                    if parsed.get("groups"):
                        st.session_state["cp_groups"] = [
                            {"label": g["label"], "count": int(g["count"]), "dietary_type": g["dietary_type"]}
                            for g in parsed["groups"]
                        ]
                    if parsed.get("budget"):
                        st.session_state["cp_budget_val"] = float(parsed["budget"])
                    if parsed.get("location"):
                        st.session_state["cp_city_input"] = parsed["location"]
                        st.session_state["cp_locate"] = "City name"
                    st.success(
                        f"Parsed {len(parsed.get('groups') or [])} group(s) from your document."
                    )
                    if parsed.get("raw_text_preview"):
                        st.caption(f"Extracted text preview: *{parsed['raw_text_preview'][:200]}…*")
                except APIError as exc:
                    st.error(f"Parse failed: {exc.detail}")

        # Offer to save parsed result to an event
        if _cp_all_events and st.session_state.get("cp_groups"):
            with st.expander("💾 Save to event (optional)"):
                _cp_save_opts = {"— don't save —": None} | {
                    f"{e['title']} ({e['date_time'][:10]})": e for e in _cp_all_events
                }
                _cp_save_label = st.selectbox("Save catering brief to event", list(_cp_save_opts.keys()), key="cp_save_to_event")
                _cp_save_ev = _cp_save_opts[_cp_save_label]
                if _cp_save_ev and st.button("💾 Save", key="cp_do_save"):
                    try:
                        groups_text = "Catering requirements:\n" + "\n".join(
                            f"- {g['label']}: {g['count']} people, {g['dietary_type']}"
                            for g in (st.session_state.get("cp_groups") or [])
                        )
                        client.save_event_catering_brief(_cp_save_ev["id"], groups_text)
                        st.success(f"✅ Saved to **{_cp_save_ev['title']}**.")
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    st.markdown("---")

    # ── Section 2: Location ───────────────────────────────────────────────────
    st.markdown("#### 📍 Location")
    cp_locate = st.radio("Locate by", ["City name", "Lat / Lon"], horizontal=True, key="cp_locate")

    cp_lat, cp_lon = None, None
    if cp_locate == "City name":
        if "cp_city_input" not in st.session_state:
            st.session_state["cp_city_input"] = "London"
        cp_city_val = st.text_input(
            "City *",
            key="cp_city_input",
            placeholder="e.g. London, Manchester",
        )
    else:
        _ll1, _ll2 = st.columns(2)
        cp_lat = _ll1.number_input("Latitude", value=51.5074, format="%.6f", key="cp_lat")
        cp_lon = _ll2.number_input("Longitude", value=-0.1278, format="%.6f", key="cp_lon")

    st.markdown("---")

    # ── Section 3: Dietary groups editor ─────────────────────────────────────
    st.markdown("#### 👥 Dietary Groups")

    if "cp_groups" not in st.session_state:
        st.session_state["cp_groups"] = [
            {"label": "All Guests", "count": 100, "dietary_type": "non-veg"}
        ]

    groups_to_delete = []
    for gi, grp in enumerate(st.session_state["cp_groups"]):
        gc1, gc2, gc3, gc4 = st.columns([3, 1, 2, 1])
        with gc1:
            new_label = st.text_input(
                "Group name", value=grp["label"], key=f"cp_label_{gi}",
                label_visibility="collapsed",
                placeholder="e.g. Vegetarian Guests",
            )
            st.session_state["cp_groups"][gi]["label"] = new_label
        with gc2:
            new_count = st.number_input(
                "People", value=int(grp["count"]), min_value=1, step=10,
                key=f"cp_count_{gi}", label_visibility="collapsed",
            )
            st.session_state["cp_groups"][gi]["count"] = int(new_count)
        with gc3:
            cur_dtype = grp["dietary_type"] if grp["dietary_type"] in _DIETARY_OPTIONS else "non-veg"
            new_dtype = st.selectbox(
                "Dietary type", _DIETARY_OPTIONS,
                index=_DIETARY_OPTIONS.index(cur_dtype),
                key=f"cp_dtype_{gi}", label_visibility="collapsed",
            )
            st.session_state["cp_groups"][gi]["dietary_type"] = new_dtype
        with gc4:
            if gi > 0 and st.button("✕", key=f"cp_del_{gi}"):
                groups_to_delete.append(gi)

    for di in reversed(groups_to_delete):
        st.session_state["cp_groups"].pop(di)
        st.rerun()

    col_add, col_total = st.columns([1, 3])
    with col_add:
        if len(st.session_state["cp_groups"]) < 5:
            if st.button("➕ Add Group", key="cp_add_group"):
                st.session_state["cp_groups"].append(
                    {"label": f"Group {len(st.session_state['cp_groups']) + 1}", "count": 50, "dietary_type": "non-veg"}
                )
                st.rerun()
    with col_total:
        total_pax = sum(g["count"] for g in st.session_state["cp_groups"])
        st.metric("Total headcount", total_pax)

    st.markdown("---")

    # ── Section 4: Budget ─────────────────────────────────────────────────────
    st.markdown("#### 💷 Budget (optional)")
    cp_budget = st.number_input(
        "Total food budget (£)",
        min_value=0.0,
        value=float(st.session_state.get("cp_budget_val", 0.0)),
        step=100.0,
        format="%.0f",
        key="cp_budget_input",
        help="Leave at 0 to skip budget tracking. Budget is used to estimate whether vendors fit your spend.",
    )

    st.markdown("---")

    # ── Section 5: Find vendors ───────────────────────────────────────────────
    if st.button("🔍 Find Matching Vendors", type="primary", key="cp_find"):
        lat_use, lon_use = cp_lat, cp_lon
        if cp_locate == "City name":
            city_name = cp_city_val.strip()
            if not city_name:
                st.error("Please enter a city name.")
                st.stop()
            with st.spinner(f"Geocoding '{city_name}'…"):
                try:
                    geo = client.mcp_geocode_city(city_name)
                    lat_use = float(geo.get("lat") or 0)
                    lon_use = float(geo.get("lon") or 0)
                except Exception:
                    lat_use, lon_use = 0.0, 0.0
            if not lat_use and not lon_use:
                st.error(f"Could not geocode '{city_name}'. Switch to Lat/Lon mode and enter coordinates manually.")
                st.stop()

        groups_payload = list(st.session_state.get("cp_groups") or [])
        if not groups_payload:
            st.error("Add at least one dietary group.")
            st.stop()

        budget_send = float(cp_budget) if cp_budget and cp_budget > 0 else None

        with st.spinner(f"Searching Feedr.co vendors near ({lat_use:.4f}, {lon_use:.4f})…"):
            try:
                cp_result = client.match_catering_vendors(lat_use, lon_use, groups_payload, budget_send)
                st.session_state["cp_result"] = cp_result
                st.session_state["cp_result_coords"] = (lat_use, lon_use)
                st.session_state["cp_result_budget"] = budget_send
            except APIError as exc:
                st.error(f"Search failed: {exc.detail}")

    # ── Section 6: Results ────────────────────────────────────────────────────
    cp_result = st.session_state.get("cp_result")
    if cp_result:
        groups_out = cp_result.get("groups") or []
        total_fetched = cp_result.get("total_vendors_fetched", 0)
        result_pax = sum(g.get("count", 0) for g in groups_out)
        result_budget = st.session_state.get("cp_result_budget")

        st.markdown("---")

        # Summary row
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Total Guests", result_pax)
        sc2.metric("Dietary Groups", len(groups_out))
        sc3.metric("Vendors Searched", total_fetched)
        if result_budget and result_pax:
            sc4.metric("Budget / Head", f"£{result_budget / result_pax:.2f}")

        # Budget tracker
        bs = cp_result.get("budget_summary")
        if bs and bs.get("min_estimated_total") is not None:
            bst1, bst2, bst3 = st.columns(3)
            bst1.metric("Total Budget", f"£{bs['total_budget']:,.0f}")
            bst2.metric("Min Estimated Total", f"£{bs['min_estimated_total']:,.0f}")
            if bs["within_budget"]:
                bst3.metric("Remaining", f"£{bs['remaining']:,.0f}", delta="within budget")
                st.success("✅ Your budget covers the cheapest available options for all groups.")
            else:
                bst3.metric("Over Budget by", f"£{bs['over_by']:,.0f}", delta_color="inverse")
                st.error("⚠️ Estimated minimum cost exceeds your budget.")

        st.markdown("---")

        # Per-group results
        _DTYPE_ICON = {
            "vegan": "🌱", "vegetarian": "🥗", "halal": "☪️",
            "kosher": "✡️", "non-veg": "🍖", "gluten-free": "🌾",
        }

        for grp in groups_out:
            dtype_icon = _DTYPE_ICON.get(grp.get("dietary_type", ""), "🍽️")
            expander_label = (
                f"{dtype_icon} **{grp['label']}** — {grp['count']} people "
                f"({grp['dietary_type']}) — {grp['match_count']} vendor(s) matched"
            )
            with st.expander(expander_label, expanded=grp["match_count"] > 0):
                if not grp.get("matched_vendors"):
                    st.warning(
                        f"No vendors found for **{grp['label']}** ({grp['dietary_type']}). "
                        "Try a different location or dietary type."
                    )
                else:
                    for v in grp["matched_vendors"]:
                        dist = v.get("distance_km")
                        dist_str = f"{dist:.2f} km" if dist is not None else "—"

                        # Price string with headcount estimate
                        pph = v.get("price_per_head")
                        count = grp["count"]
                        if pph and count:
                            price_str = f"£{pph:.2f}/head × {count} pax = **{v.get('estimated_cost_str', '—')} est. total**"
                        elif v.get("price_range"):
                            label_p = _PRICE_LABELS.get(v["price_range"], "")
                            price_str = f"{v['price_range']} {label_p} · price on request"
                        else:
                            price_str = "Price on request"

                        # Dietary badges from specializations
                        specs = list(v.get("specializations") or [])
                        tag_lower = [t.lower() for t in (v.get("tags") or [])]
                        for kw in _DIETARY_ICONS:
                            if kw not in specs and (kw in tag_lower or kw.replace("-", " ") in tag_lower):
                                specs.append(kw)

                        # Rating
                        rating = v.get("rating")
                        if rating:
                            filled = min(int(round(rating)), 5)
                            rating_str = "★" * filled + "☆" * (5 - filled) + f" {rating:.1f}"
                        else:
                            rating_str = ""

                        # Card layout
                        card_l, card_r = st.columns([4, 1])
                        with card_r:
                            if v.get("logo"):
                                st.image(v["logo"], use_container_width=True)
                        with card_l:
                            st.markdown(f"### {v.get('name', '—')}  `{dist_str}`")
                            if v.get("address"):
                                st.markdown(f"📍 {v['address']}")
                            info_row = [f"💷 {price_str}"]
                            if v.get("cuisine"):
                                info_row.append(f"🍴 {v['cuisine'].title()}")
                            if rating_str:
                                info_row.append(rating_str)
                            st.markdown("  ·  ".join(info_row))
                            if specs:
                                st.markdown(" ".join(
                                    f"`{_DIETARY_ICONS.get(s, ('',''))[0]} {_DIETARY_ICONS.get(s, ('',s.title()))[1]}`"
                                    for s in specs
                                ))
                            if v.get("description"):
                                st.markdown(f"*{v['description'][:200]}*")

                            # Buttons
                            b1, b2 = st.columns(2)
                            permalink = v.get("permalink") or ""
                            cp_menu_key = f"cp_menu_{permalink}_{grp['dietary_type']}"
                            with b1:
                                if permalink and st.button(
                                    "📋 Load Full Menu",
                                    key=f"cp_load_{permalink}_{grp['dietary_type']}_{v.get('feedr_id','')}",
                                ):
                                    with st.spinner("Fetching full menu…"):
                                        try:
                                            detail = client.get_vendor_detail(permalink)
                                            st.session_state[cp_menu_key] = detail
                                        except Exception as _ex:
                                            st.error(f"Failed to load menu: {_ex}")
                            with b2:
                                if v.get("website"):
                                    st.link_button("🔗 Open on Feedr.co", v["website"])

                        # Inline full menu (if loaded)
                        detail_cp = st.session_state.get(cp_menu_key)
                        if detail_cp:
                            with st.expander(
                                f"📋 {detail_cp.get('name')} — Full Menu ({detail_cp.get('menu_item_count', 0)} items)",
                                expanded=True,
                            ):
                                gallery = detail_cp.get("images") or []
                                if gallery:
                                    gi_cols = st.columns(min(len(gallery), 3))
                                    for ic, img_url in enumerate(gallery[:3]):
                                        gi_cols[ic].image(img_url, use_container_width=True)
                                if detail_cp.get("quote"):
                                    st.markdown(f"> *{detail_cp['quote']}*")
                                if detail_cp.get("tips"):
                                    with st.expander("ℹ️ Dietary & Allergen Info"):
                                        st.text(detail_cp["tips"][:600])
                                st.divider()
                                grouped_menu = detail_cp.get("menu_grouped") or {}
                                for sec_name, sec_items in grouped_menu.items():
                                    st.markdown(f"#### {sec_name}  ({len(sec_items)} items)")
                                    for idx in range(0, len(sec_items), 2):
                                        mi1, mi2 = st.columns(2)
                                        for col_ref, item in zip([mi1, mi2], sec_items[idx:idx+2]):
                                            with col_ref:
                                                im_c, tx_c = col_ref.columns([1, 3])
                                                if item.get("image"):
                                                    im_c.image(item["image"], width=72)
                                                with tx_c:
                                                    price_i = f"£{item['price_gbp']:.2f}" if item.get("price_gbp") else "—"
                                                    hot_i = "🔥 " if item.get("is_hot") else ""
                                                    st.markdown(f"**{hot_i}{item['name']}**  `{price_i}`")
                                                    badges_i = [v2 for k2, v2 in _DIETARY_BADGE_MAP.items() if item.get(k2)]
                                                    if badges_i:
                                                        st.markdown(" ".join(f"`{ic} {lb}`" for ic, lb in badges_i))
                                                    allergens_i = [lbl for k2, lbl in _ALLERGEN_MAP.items() if item.get(k2)]
                                                    if allergens_i:
                                                        st.caption("⚠️ " + " · ".join(allergens_i))
                                                    if item.get("kcal"):
                                                        st.caption(f"🔥 {int(item['kcal'])} kcal")
                                                    if item.get("description"):
                                                        st.caption(item["description"][:120])
                                    st.markdown("---")
                                if st.button("✕ Close menu", key=f"cp_close_{permalink}_{grp['dietary_type']}"):
                                    st.session_state.pop(cp_menu_key, None)
                                    st.rerun()

                        st.divider()

        # Clear button
        st.markdown("---")
        if st.button("🗑️ Clear all results", key="cp_clear"):
            for k in list(st.session_state.keys()):
                if k.startswith(("cp_result", "cp_menu_", "cp_groups", "cp_budget", "cp_city")):
                    st.session_state.pop(k, None)
            st.rerun()
