import json
import re
import streamlit as st
from modules.auth import require_auth, show_sidebar
from modules.rag import is_configured, extract_event_requirements
from modules.indexing import (
    index_event_plan,
    extract_text_from_pdf,
    extract_text_from_docx,
)
from modules.api_fetcher import (
    ALL_CATEGORIES,
    fetch_all_city_venues,
    get_city_coords,
    GEOAPIFY_API_KEY,
)

st.set_page_config(
    page_title="Smart Event Planner — AI Event Manager",
    page_icon="🎯",
    layout="wide",
)
require_auth()
show_sidebar()

st.title("🎯 Smart Event Planner")
st.markdown(
    "Upload an event brief or paste your requirements. "
    "The AI extracts what it needs, fetches real venues nearby, and indexes everything "
    "so you can chat with your complete event dataset."
)

if not is_configured():
    st.warning("Azure OpenAI is not configured — set `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`.")
    st.stop()

# ── Session state keys ────────────────────────────────────────────────────────
for key in ("sp_raw_text", "sp_requirements", "sp_venues", "sp_source_counts", "sp_indexed_collection"):
    if key not in st.session_state:
        st.session_state[key] = None

# ── Smart event-type → category defaults ─────────────────────────────────────
_EVENT_CATEGORY_MAP = {
    "corporate":      ["Conference & Event Venues"],
    "networking":     ["Conference & Event Venues"],
    "conference":     ["Conference & Event Venues"],
    "seminar":        ["Conference & Event Venues"],
    "business":       ["Conference & Event Venues"],
    "agm":            ["Conference & Event Venues"],
    "product launch": ["Conference & Event Venues"],
    "product_launch": ["Conference & Event Venues"],
    "exhibition":     ["Arts & Entertainment"],
    "wedding":        ["Conference & Event Venues", "Hotels & Accommodation"],
    "gala":           ["Conference & Event Venues", "Hotels & Accommodation"],
    "black-tie":      ["Conference & Event Venues", "Hotels & Accommodation"],
    "birthday":       ["Restaurants & Cafes", "Bars & Nightlife"],
    "party":          ["Restaurants & Cafes", "Bars & Nightlife"],
    "graduation":     ["Restaurants & Cafes", "Bars & Nightlife"],
    "sports":         ["Sports & Recreation"],
    "concert":        ["Arts & Entertainment"],
    "theatre":        ["Arts & Entertainment"],
}

def _smart_default_categories(event_type: str, ai_cats: list) -> list:
    valid = [c for c in ai_cats if c in ALL_CATEGORIES]
    if valid:
        return valid
    et = (event_type or "").lower()
    for keyword, cats in _EVENT_CATEGORY_MAP.items():
        if keyword in et:
            return cats
    return ["Conference & Event Venues"]


# ── Budget split tables ───────────────────────────────────────────────────────

_BUDGET_SPLITS: dict = {
    "corporate": [
        ("🏢 Venue Hire",              0.35),
        ("🍽️ Catering & Food",         0.40),
        ("🎤 AV & Equipment",          0.10),
        ("🎨 Decor & Branding",        0.08),
        ("⚠️ Contingency",             0.07),
    ],
    "networking": [
        ("🏢 Venue Hire",              0.40),
        ("🍽️ Catering & Drinks",       0.38),
        ("🎤 AV & Equipment",          0.10),
        ("🎨 Branding & Print",        0.07),
        ("⚠️ Contingency",             0.05),
    ],
    "conference": [
        ("🏢 Venue Hire",              0.35),
        ("🍽️ Catering",               0.25),
        ("🎤 AV & Technology",         0.18),
        ("🖨️ Materials & Print",       0.12),
        ("⚠️ Contingency",             0.10),
    ],
    "wedding": [
        ("🏢 Venue Hire",              0.30),
        ("🍽️ Catering & Food",         0.35),
        ("📸 Photography & Video",     0.10),
        ("💐 Flowers & Decor",         0.12),
        ("🎵 Entertainment",           0.08),
        ("⚠️ Contingency",             0.05),
    ],
    "gala": [
        ("🏢 Venue Hire",              0.32),
        ("🍽️ Catering & Food",         0.38),
        ("🎵 Entertainment",           0.12),
        ("💐 Decor & Flowers",         0.10),
        ("⚠️ Contingency",             0.08),
    ],
    "birthday": [
        ("🏢 Venue Hire",              0.25),
        ("🍽️ Food & Drink",            0.35),
        ("🎵 Entertainment",           0.20),
        ("🎨 Decor",                   0.12),
        ("⚠️ Contingency",             0.08),
    ],
    "graduation": [
        ("🏢 Venue Hire",              0.28),
        ("🍽️ Catering & Food",         0.38),
        ("📸 Photography",             0.12),
        ("🎨 Decor",                   0.12),
        ("⚠️ Contingency",             0.10),
    ],
    "exhibition": [
        ("🏢 Venue Hire",              0.35),
        ("🎨 Stand & Display Setup",   0.25),
        ("🍽️ Catering",               0.15),
        ("🎤 AV & Tech",               0.15),
        ("⚠️ Contingency",             0.10),
    ],
}

def _event_budget_key(event_type: str) -> str:
    et = (event_type or "").lower()
    for key in _BUDGET_SPLITS:
        if key in et:
            return key
    if any(k in et for k in ["corporate", "business", "annual"]):
        return "corporate"
    if any(k in et for k in ["network", "connect"]):
        return "networking"
    if any(k in et for k in ["wedding", "marriage"]):
        return "wedding"
    if any(k in et for k in ["birthday", "party"]):
        return "birthday"
    if any(k in et for k in ["conference", "seminar"]):
        return "conference"
    return "corporate"

def _parse_budget_amount(budget_str: str) -> tuple:
    """Return (currency_symbol, amount_int)."""
    if not budget_str:
        return "", 0
    currency = "£" if "£" in budget_str else ("$" if "$" in budget_str else ("€" if "€" in budget_str else ""))
    nums = re.findall(r"[\d,]+", budget_str)
    if nums:
        return currency, int(nums[0].replace(",", ""))
    return currency, 0

def _parse_first_num(s) -> int:
    nums = re.findall(r"\d+", str(s or ""))
    return int(nums[0]) if nums else 0


# ── Venue card renderer ───────────────────────────────────────────────────────

def _map_thumbnail_url(lat, lon) -> str:
    return (
        f"https://maps.geoapify.com/v1/staticmap"
        f"?style=osm-bright-smooth&width=600&height=220"
        f"&center=lonlat:{lon},{lat}&zoom=16"
        f"&marker=lonlat:{lon},{lat};color:%23e74c3c;size:large"
        f"&apiKey={GEOAPIFY_API_KEY}"
    )


def _render_venue_card(v: dict, required_guests: int = 0):
    """Render a single venue as a rich card with map, capacity, facilities, and contact."""
    name       = v.get("name") or "Unknown Venue"
    vtype      = v.get("type") or "Venue"
    address    = v.get("address") or ""
    capacity   = v.get("capacity") or ""
    phone      = v.get("phone") or ""
    email      = v.get("email") or ""
    website    = v.get("website") or ""
    desc       = v.get("description") or ""
    wheelchair = v.get("wheelchair") or ""
    internet   = v.get("internet_access") or ""
    outdoor    = v.get("outdoor_seating") or ""
    stars      = v.get("stars") or ""
    rooms      = v.get("rooms") or ""
    operator   = v.get("operator") or ""
    source     = v.get("source") or ""
    lat = v.get("lat")
    lon = v.get("lon")

    src_icon = {"Geoapify": "🌐", "OSM": "🗺️", "Foursquare": "📍"}.get(source, "📌")

    with st.container(border=True):
        # ── Map thumbnail ──────────────────────────────────────────────────────
        if lat and lon:
            try:
                st.image(_map_thumbnail_url(lat, lon), use_container_width=True)
            except Exception:
                pass

        # ── Name & type ────────────────────────────────────────────────────────
        st.markdown(f"#### {name}")
        operator_str = f" · operated by {operator}" if operator else ""
        st.caption(f"{src_icon} **{vtype}**{operator_str} · via {source}")

        # ── Address ───────────────────────────────────────────────────────────
        if address:
            st.markdown(f"📍 {address}")

        # ── Capacity (with seating/standing estimates) ─────────────────────────
        if capacity:
            total = _parse_first_num(capacity)
            if total:
                seating  = int(total * 0.65)
                standing = int(total * 1.15)
                # Highlight if fits the requirement
                fits = seating >= required_guests if required_guests else True
                cap_label = "✅ **Capacity:**" if fits else "⚠️ **Capacity:**"
                st.markdown(
                    f"{cap_label} {capacity}  \n"
                    f"&nbsp;&nbsp;🪑 **Seated** (theatre/cabaret): ~{seating:,} guests  \n"
                    f"&nbsp;&nbsp;🧍 **Standing** / reception: ~{standing:,} guests  \n"
                    f"&nbsp;&nbsp;<small>*Configurations are estimates — confirm with venue*</small>",
                    unsafe_allow_html=True,
                )
                if required_guests and seating < required_guests:
                    st.warning(f"Seated estimate ({seating:,}) is below your {required_guests:,} guest requirement.")
            else:
                st.markdown(f"👥 **Capacity:** {capacity}")
        else:
            if required_guests:
                st.caption("👥 Capacity not listed — contact venue to confirm space for your guest count")
            else:
                st.caption("👥 Capacity: not listed")

        # ── Description ───────────────────────────────────────────────────────
        if desc:
            st.markdown(f"📝 *{desc[:220]}{'…' if len(desc) > 220 else ''}*")

        # ── Facilities ────────────────────────────────────────────────────────
        facilities = []
        if wheelchair and wheelchair.lower() in ("yes", "designated", "limited"):
            facilities.append("♿ Wheelchair accessible")
        if internet and internet.lower() not in ("no", ""):
            facilities.append("📶 WiFi available")
        if outdoor and outdoor.lower() == "yes":
            facilities.append("🌿 Outdoor area")
        if stars:
            facilities.append(f"⭐ {stars}-star rated")
        if rooms:
            facilities.append(f"🏨 {rooms} rooms")

        if facilities:
            st.markdown("**Facilities:** " + " &nbsp;·&nbsp; ".join(facilities))

        # ── Pricing note ──────────────────────────────────────────────────────
        st.markdown(
            "💷 **Hourly/event rate:** Contact the venue directly — "
            "pricing varies by duration, guest count, and package."
        )

        # ── Contact ───────────────────────────────────────────────────────────
        contact_parts = []
        if phone:
            contact_parts.append(f"📞 `{phone}`")
        if email:
            contact_parts.append(f"✉️ `{email}`")
        if website:
            contact_parts.append(f"[🌐 Website]({website})")

        if contact_parts:
            st.markdown("**Contact:** " + " &nbsp;|&nbsp; ".join(contact_parts))
        else:
            st.caption("No contact info available — search venue name online")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Input
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("## Step 1 — Describe Your Event")

input_method = st.radio("Input method", ["Upload file (PDF / DOCX / TXT)", "Type / paste requirements"], horizontal=True)
raw_text = ""

if input_method.startswith("Upload"):
    uploaded = st.file_uploader("Upload event brief", type=["pdf", "docx", "txt"])
    if uploaded:
        ext = uploaded.name.rsplit(".", 1)[-1].lower()
        file_bytes = uploaded.read()
        if ext == "pdf":
            raw_text = extract_text_from_pdf(file_bytes)
        elif ext == "docx":
            raw_text = extract_text_from_docx(file_bytes)
        else:
            raw_text = file_bytes.decode("utf-8", errors="ignore")

        with st.expander(f"📄 Extracted text from **{uploaded.name}** ({len(raw_text.split())} words)"):
            st.text(raw_text[:3000] + ("…" if len(raw_text) > 3000 else ""))
else:
    raw_text = st.text_area(
        "Paste your event requirements",
        height=200,
        placeholder=(
            "e.g. We need a venue near Camden Market for 250 guests on 18 Sep 2026. "
            "Budget £32,500. Catering: 100 vegetarian, 120 non-veg, 30 vegan. "
            "Looking for a conference or event hall within 1 km."
        ),
    )

extract_btn = st.button(
    "✨ Extract Requirements with AI",
    type="primary",
    disabled=not raw_text.strip(),
)

if extract_btn and raw_text.strip():
    with st.spinner("AI is reading your brief…"):
        try:
            reqs = extract_event_requirements(raw_text)
            st.session_state["sp_raw_text"] = raw_text
            st.session_state["sp_requirements"] = reqs
            st.session_state["sp_venues"] = None
            st.session_state["sp_source_counts"] = None
            st.session_state["sp_indexed_collection"] = None
        except Exception as e:
            st.error(f"Extraction failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Review & edit extracted requirements
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state["sp_requirements"]:
    reqs: dict = st.session_state["sp_requirements"]

    st.markdown("---")
    st.markdown("## Step 2 — Review & Edit Requirements")
    st.caption("The AI filled these in from your brief — adjust anything before fetching venues.")

    col1, col2 = st.columns(2)
    with col1:
        event_name = st.text_input("Event name", value=reqs.get("event_name", ""))
        city = st.text_input("City (for geocoding)", value=reqs.get("city", ""))
        location_hint = st.text_input(
            "Specific area / landmark",
            value=reqs.get("location_hint", reqs.get("city", "")),
            help="Used to geocode a precise starting point, e.g. 'Camden Market'",
        )
        radius_km = st.slider("Search radius (km)", 1, 20, int(reqs.get("radius_km", 2)))
    with col2:
        guest_count = st.text_input("Guest count", value=str(reqs.get("guest_count", "") or ""))
        budget = st.text_input("Budget", value=reqs.get("budget", "") or "")
        event_date = st.text_input("Event date", value=reqs.get("event_date", "") or "")
        event_type = st.text_input("Event type", value=reqs.get("event_type", ""))

    smart_defaults = _smart_default_categories(event_type, reqs.get("categories", []))

    selected_cats = st.multiselect(
        "Venue categories to search",
        options=ALL_CATEGORIES,
        default=smart_defaults,
        help="AI selected these based on your event type. Add more if needed.",
    )

    collection_slug = st.text_input(
        "Collection name (slug)",
        value=reqs.get("collection_slug", "my_event")[:20],
        max_chars=20,
        help="Short ID for this event's data collection",
    )

    st.markdown("---")
    fetch_btn = st.button(
        f"🔍 Fetch Venues near **{location_hint or city}** (radius {radius_km} km)",
        type="primary",
        disabled=not city.strip() or not selected_cats,
    )

    if fetch_btn:
        with st.spinner(f"Geocoding '{location_hint or city}' and fetching venues…"):
            coords = get_city_coords(location_hint.strip()) if location_hint.strip() else None
            if not coords and city.strip():
                coords = get_city_coords(city.strip())

            if not coords:
                st.error(f"Could not find coordinates for **{location_hint or city}**. Check the spelling.")
            else:
                lat, lon = coords
                st.info(f"📍 Located **{location_hint or city}** at ({lat:.4f}, {lon:.4f})")

                venues, source_counts = fetch_all_city_venues(
                    city=city.strip() or location_hint.strip(),
                    categories=selected_cats,
                    radius_km=radius_km,
                    use_foursquare=True,
                    use_geoapify=True,
                    enrich_details=True,
                    max_venues=500,
                    coords=coords,
                )
                st.session_state["sp_venues"] = venues
                st.session_state["sp_source_counts"] = source_counts
                st.session_state["sp_requirements"].update({
                    "event_name": event_name,
                    "city": city,
                    "location_hint": location_hint,
                    "radius_km": radius_km,
                    "guest_count": guest_count,
                    "budget": budget,
                    "event_date": event_date,
                    "event_type": event_type,
                    "categories": selected_cats,
                    "collection_slug": collection_slug,
                })

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Preview venues & index
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state["sp_venues"] is not None:
    venues   = st.session_state["sp_venues"]
    counts   = st.session_state["sp_source_counts"] or {}
    reqs     = st.session_state["sp_requirements"] or {}
    ev_type  = reqs.get("event_type", "")

    st.markdown("---")
    st.markdown("## Step 3 — Preview Venues & Index")

    if not venues:
        st.warning("No venues found. Try increasing the radius or adding more categories.")
    else:
        # ── Source breakdown & overview metrics ───────────────────────────────
        src_cols = st.columns(max(len(counts), 1))
        for col, (src, cnt) in zip(src_cols, counts.items()):
            col.metric(src, cnt)

        raw_text_stored = st.session_state["sp_raw_text"] or ""
        doc_chunk_count = max(1, len(raw_text_stored.split()) // 400 + 1) if raw_text_stored.strip() else 0

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Venues fetched", len(venues))
        col_b.metric("Document chunks (approx)", doc_chunk_count)
        col_c.metric("Total chunks to index", len(venues) + doc_chunk_count)

        with_cap     = sum(1 for v in venues if v.get("capacity"))
        with_contact = sum(1 for v in venues if v.get("phone") or v.get("email") or v.get("website"))
        st.caption(
            f"✅ Capacity data: **{with_cap}/{len(venues)}** venues &nbsp;|&nbsp; "
            f"📞 Contact info: **{with_contact}/{len(venues)}** venues"
        )

        st.markdown("---")

        # ── Parse guest count & budget from requirements ───────────────────────
        try:
            required_guests = int(str(reqs.get("guest_count") or "").replace(",", "")) or None
        except (ValueError, TypeError):
            required_guests = None

        budget_str = reqs.get("budget") or ""

        # ── If details are missing, ask the user ──────────────────────────────
        missing_guests = required_guests is None
        missing_budget = not budget_str.strip()

        if missing_guests or missing_budget:
            st.markdown("### ❓ Missing Details")
            st.caption("These weren't found in your document — fill them in to enable capacity filtering and budget split.")
            ask_c1, ask_c2 = st.columns(2)

            with ask_c1:
                if missing_guests:
                    manual_guests = st.number_input(
                        "👥 How many guests are you expecting?",
                        min_value=1, max_value=10000,
                        value=None,
                        placeholder="e.g. 200",
                        key="sp_manual_guests",
                    )
                    if manual_guests:
                        required_guests = int(manual_guests)
                else:
                    st.success(f"👥 Guest count from document: **{required_guests:,}**")

            with ask_c2:
                if missing_budget:
                    manual_budget = st.text_input(
                        "💰 What is your total budget?",
                        placeholder="e.g. £25,000",
                        key="sp_manual_budget",
                    )
                    budget_str = manual_budget or ""
                else:
                    st.success(f"💰 Budget from document: **{budget_str}**")

            st.markdown("---")

        # ── Budget split breakdown ─────────────────────────────────────────────
        currency, budget_amount = _parse_budget_amount(budget_str)
        if budget_amount > 0:
            budget_key = _event_budget_key(ev_type)
            splits     = _BUDGET_SPLITS.get(budget_key, _BUDGET_SPLITS["corporate"])

            with st.expander(f"💰 Suggested Budget Split — {budget_str} total", expanded=True):
                rows = []
                running_total = 0
                for cat_name, pct in splits:
                    amount = int(budget_amount * pct)
                    running_total += amount
                    rows.append({
                        "Category":             cat_name,
                        "% of Budget":          f"{int(pct * 100)}%",
                        f"Amount ({currency})": f"{currency}{amount:,}",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
                st.caption(
                    f"Allocated: **{currency}{running_total:,}** of **{budget_str}** · "
                    f"Split based on typical **{budget_key}** event spending. "
                    "Adjust percentages based on your priorities."
                )

            st.markdown("---")

        # ── Capacity filter ────────────────────────────────────────────────────
        if required_guests:
            suitable    = []
            unknown_cap = []
            too_small   = []

            for v in venues:
                total = _parse_first_num(v.get("capacity"))
                if total == 0:
                    unknown_cap.append(v)
                elif total >= required_guests:
                    suitable.append(v)
                else:
                    too_small.append(v)

            display_venues = suitable + unknown_cap

            # Filter summary
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("✅ Fits your guest count", len(suitable))
            fc2.metric("❓ Capacity unknown", len(unknown_cap))
            fc3.metric("❌ Too small (hidden)", len(too_small))

            if suitable:
                st.success(
                    f"Showing **{len(suitable)}** venues with capacity ≥ **{required_guests:,} guests** "
                    f"+ **{len(unknown_cap)}** venues with unconfirmed capacity."
                )
            elif unknown_cap:
                st.warning(
                    f"No venues with confirmed capacity ≥ {required_guests:,}. "
                    f"Showing **{len(unknown_cap)}** venues with unknown capacity — contact them to confirm."
                )
            else:
                st.error(
                    f"No venues found for {required_guests:,} guests in this area. "
                    "Try increasing the search radius in Step 2."
                )

            # Collapsed list of venues that are too small
            if too_small:
                with st.expander(f"View {len(too_small)} venues that are too small for {required_guests:,} guests"):
                    for v in too_small:
                        total = _parse_first_num(v.get("capacity"))
                        seated = int(total * 0.65) if total else 0
                        st.markdown(
                            f"- **{v['name']}** — capacity: {v.get('capacity') or 'unknown'} "
                            f"(seated ~{seated:,}) · {v.get('address', '')}"
                        )
        else:
            display_venues = venues
            st.info(
                "💡 Enter a guest count above to filter venues by capacity. "
                "All venues are shown for now."
            )

        st.markdown("---")

        # ── Venue card grid with pagination ──────────────────────────────────
        if display_venues:
            CARDS_PER_PAGE = 12
            if "sp_venue_page" not in st.session_state:
                st.session_state["sp_venue_page"] = 0

            total_pages  = max(1, (len(display_venues) - 1) // CARDS_PER_PAGE + 1)
            current_page = min(st.session_state["sp_venue_page"], total_pages - 1)

            # Pagination controls
            pg_c1, pg_c2, pg_c3 = st.columns([1, 4, 1])
            if pg_c1.button("◀ Prev", disabled=current_page == 0, key="prev_page"):
                st.session_state["sp_venue_page"] = current_page - 1
                st.rerun()
            pg_c2.markdown(
                f"<div style='text-align:center; padding-top:8px;'>"
                f"Showing <b>{current_page * CARDS_PER_PAGE + 1}–"
                f"{min((current_page + 1) * CARDS_PER_PAGE, len(display_venues))}</b> of "
                f"<b>{len(display_venues)}</b> venues &nbsp;·&nbsp; "
                f"Page {current_page + 1}/{total_pages}"
                f"</div>",
                unsafe_allow_html=True,
            )
            if pg_c3.button("Next ▶", disabled=current_page >= total_pages - 1, key="next_page"):
                st.session_state["sp_venue_page"] = current_page + 1
                st.rerun()

            page_venues = display_venues[
                current_page * CARDS_PER_PAGE:(current_page + 1) * CARDS_PER_PAGE
            ]

            # 2-column card grid
            for i in range(0, len(page_venues), 2):
                c1, c2 = st.columns(2)
                with c1:
                    _render_venue_card(page_venues[i], required_guests=required_guests or 0)
                if i + 1 < len(page_venues):
                    with c2:
                        _render_venue_card(page_venues[i + 1], required_guests=required_guests or 0)

        # ── Full raw table (collapsed) ─────────────────────────────────────────
        with st.expander(f"📊 Full venue table ({len(venues)} rows — all including filtered-out)"):
            st.dataframe(
                [
                    {
                        "Name":       v["name"],
                        "Type":       v["type"],
                        "Capacity":   v.get("capacity") or "—",
                        "Address":    v.get("address") or "—",
                        "Phone":      v.get("phone") or "—",
                        "Email":      v.get("email") or "—",
                        "Website":    v.get("website") or "—",
                        "Wheelchair": v.get("wheelchair") or "—",
                        "WiFi":       v.get("internet_access") or "—",
                        "Stars":      v.get("stars") or "—",
                        "Source":     v["source"],
                    }
                    for v in venues
                ],
                use_container_width=True,
            )

        # ── Index button ──────────────────────────────────────────────────────
        st.markdown("---")
        collection_slug = reqs.get("collection_slug", "my_event")
        event_name      = reqs.get("event_name", "Event Plan")
        city            = reqs.get("city", reqs.get("location_hint", ""))

        st.markdown(
            f"**Collection name:** `evp_u{st.session_state['user_id']}_{collection_slug[:20]}`  \n"
            f"This will contain your **event brief + {len(venues)} real venues** — "
            "ask the AI assistant anything about them."
        )

        if st.button(
            f"⚡ Index {len(venues)} venues + event brief into collection",
            type="primary",
        ):
            with st.spinner("Embedding and indexing everything…"):
                src_id, err = index_event_plan(
                    user_id=st.session_state["user_id"],
                    event_name=event_name,
                    collection_slug=collection_slug,
                    document_text=st.session_state["sp_raw_text"] or "",
                    venues=venues,
                    city=city,
                )

            if src_id:
                st.session_state["sp_indexed_collection"] = collection_slug
                st.success(
                    f"✅ **{event_name}** indexed! "
                    f"{len(venues)} venues + event brief are now searchable by the AI assistant."
                )
            else:
                st.error(f"Indexing failed: {err}")

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Inline chat
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state.get("sp_indexed_collection"):
    st.markdown("---")
    st.markdown("## Step 4 — Chat with Your Event Data")

    if "sp_chat_history" not in st.session_state:
        st.session_state["sp_chat_history"] = []

    reqs           = st.session_state["sp_requirements"] or {}
    event_name     = reqs.get("event_name", "Event Plan")
    city           = reqs.get("city", "")
    collection_slug = st.session_state["sp_indexed_collection"]
    collection_name = f"evp_u{st.session_state['user_id']}_{collection_slug[:20]}"

    for msg in st.session_state["sp_chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if not st.session_state["sp_chat_history"]:
        with st.chat_message("assistant"):
            st.markdown(
                f"Hi! I have your **{event_name}** brief and **real venue data near {city}** indexed. "
                "Ask me anything — venue options, capacity, catering, logistics, budget breakdown, etc."
            )

    prompt = st.chat_input("Ask about your event or the venues…")
    if prompt:
        st.session_state["sp_chat_history"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                from modules.guardrails import check_input, validate_output, ABUSE_MESSAGE
                from modules.agent import EventPlanningAgent
                from modules.rag import AZURE_OPENAI_DEPLOYMENT

                history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state["sp_chat_history"][:-1]
                ]

                answer_data = None

                with st.status("🔄 Agent working…", expanded=True) as status:

                    st.write("**🛡️ Input Guardrail** — abuse · spelling · sarcasm")
                    guard_in = check_input(prompt)

                    if not guard_in["allowed"]:
                        status.update(label="🚫 Blocked", state="error", expanded=True)
                        st.error(f"Blocked: {guard_in['rejection_reason']}")
                        st.session_state["sp_chat_history"].append(
                            {"role": "assistant", "content": ABUSE_MESSAGE}
                        )
                        st.stop()

                    st.write(f"&nbsp;&nbsp;✅ Category: `{guard_in['category']}`")
                    if guard_in["was_corrected"]:
                        for c in guard_in.get("corrections", []):
                            st.write(f"&nbsp;&nbsp;✏️ `{c['original']}` → `{c['corrected']}`")
                    if guard_in["is_sarcastic"]:
                        st.write(f"&nbsp;&nbsp;😏 Sarcasm — real intent: *{guard_in['real_intent']}*")

                    effective_query = guard_in["real_intent"]
                    st.divider()

                    st.write(
                        f"**🤖 Event Planning Agent** · Model: `{AZURE_OPENAI_DEPLOYMENT}` · "
                        f"Collection: `{collection_name}`"
                    )
                    st.caption("Agent decides which tools to call based on your question.")

                    _TOOL_ICONS = {
                        "rag_search":            "🔍",
                        "filter_by_capacity":    "📐",
                        "search_venues_live":    "🌐",
                        "find_catering_options": "🍽️",
                    }

                    agent = EventPlanningAgent(collection_name=collection_name, city=city)

                    for event in agent.run(effective_query, chat_history=history):
                        if event["type"] == "thinking":
                            st.caption(f"&nbsp;&nbsp;💭 {event['message']}")
                        elif event["type"] == "tool_call":
                            tool    = event["tool"]
                            args    = event["args"]
                            icon    = _TOOL_ICONS.get(tool, "🔧")
                            arg_str = " · ".join(
                                f"`{k}={v}`" for k, v in args.items()
                                if k not in ("collection_name",)
                            )
                            st.write(f"&nbsp;&nbsp;{icon} **Tool called:** `{tool}` — {arg_str}")
                        elif event["type"] == "tool_result":
                            st.write(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ Result: {event['summary']}")
                        elif event["type"] == "answer":
                            answer_data = event["data"]
                        elif event["type"] == "error":
                            st.error(event["message"])

                    st.divider()
                    st.write("**🛡️ Output Guardrail** — JSON schema validation")
                    validated       = validate_output(answer_data or {})
                    confidence_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(validated["confidence"], "⚪")
                    tools_used      = answer_data.get("tools_used", []) if answer_data else []
                    st.write(
                        f"&nbsp;&nbsp;✅ Schema valid · "
                        f"Confidence: {confidence_icon} `{validated['confidence']}` · "
                        f"Tools used: `{len(tools_used)}`"
                    )
                    st.caption(f"Interpretation: *{validated.get('query_interpretation', '')}*")

                    status.update(label="✅ Agent finished — answer ready", state="complete", expanded=False)

                st.markdown(validated["answer"])

                cols = st.columns(3)
                cols[0].caption(f"{confidence_icon} Confidence: **{validated['confidence']}**")
                if validated["sources_used"]:
                    cols[1].caption(
                        "Sources: " + ", ".join(f"*{s}*" for s in validated["sources_used"][:3])
                        + ("…" if len(validated["sources_used"]) > 3 else "")
                    )
                if tools_used:
                    cols[2].caption("Tools: " + " · ".join(f"`{t}`" for t in set(tools_used)))

                st.session_state["sp_chat_history"].append(
                    {"role": "assistant", "content": validated["answer"]}
                )

            except Exception as e:
                import traceback
                err = f"⚠️ Error: {e}"
                st.error(err)
                st.caption(traceback.format_exc())
                st.session_state["sp_chat_history"].append({"role": "assistant", "content": err})

    if st.session_state["sp_chat_history"]:
        if st.button("🗑️ Clear chat"):
            st.session_state["sp_chat_history"] = []
            st.rerun()
