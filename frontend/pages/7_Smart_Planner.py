import json
import re
import streamlit as st

from utils.auth import require_auth, show_sidebar, get_client
from utils.ui import tool_badge, data_source_badges
from client.api_client import APIError

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

_EVENT_CATEGORY_MAP = {
    "corporate": ["Conference & Event Venues"],
    "networking": ["Conference & Event Venues"],
    "conference": ["Conference & Event Venues"],
    "seminar": ["Conference & Event Venues"],
    "wedding": ["Conference & Event Venues", "Hotels & Accommodation"],
    "gala": ["Conference & Event Venues", "Hotels & Accommodation"],
    "birthday": ["Restaurants & Cafes", "Bars & Nightlife"],
    "party": ["Restaurants & Cafes", "Bars & Nightlife"],
    "graduation": ["Restaurants & Cafes", "Bars & Nightlife"],
    "sports": ["Sports & Recreation"],
    "concert": ["Arts & Entertainment"],
    "exhibition": ["Arts & Entertainment"],
}

COMMON_FACILITIES = [
    "Wi-Fi", "AV equipment", "Stage", "PA System", "Screens / Projector",
    "Wheelchair accessible", "Parking Facilities", "Air Conditioning",
    "Catering kitchen", "Outdoor area", "Breakout rooms",
    "Video Conferencing", "Dancefloor", "Bar / Alcohol license",
    "Tables & Chairs", "Natural light",
]

def _smart_default_categories(event_type: str, ai_cats: list) -> list:
    valid = [c for c in ai_cats if c in ALL_CATEGORIES]
    if valid:
        return valid
    et = (event_type or "").lower()
    for keyword, cats in _EVENT_CATEGORY_MAP.items():
        if keyword in et:
            return cats
    return ["Conference & Event Venues"]



def _parse_budget_amount(budget_str: str):
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


def _render_venue_card(v: dict, idx: int = 0, required_guests: int = 0):
    # Use enriched version if available for this card index
    enriched_map = st.session_state.get("sp_enriched", {})
    v = enriched_map.get(idx, v)

    name = v.get("name") or "Unknown Venue"
    vtype = v.get("type") or "Venue"
    address = v.get("address") or ""
    capacity = v.get("capacity") or ""
    phone = v.get("phone") or ""
    email = v.get("email") or ""
    website = v.get("website") or ""
    desc = v.get("description") or ""
    wheelchair = v.get("wheelchair") or ""
    internet = v.get("internet_access") or ""
    outdoor = v.get("outdoor_seating") or ""
    stars = v.get("stars") or ""
    rooms = v.get("rooms") or ""
    source = v.get("source") or ""
    map_url = v.get("map_thumbnail_url") or ""
    image_url = v.get("image_url") or ""
    price_day = v.get("price_per_day") or ""
    price_hour = v.get("price_per_hour") or ""
    price_range = v.get("price_range") or ""
    min_spend = v.get("min_spend") or ""

    ev_types = v.get("event_types") or []
    ev_type_match = v.get("event_type_match")
    src_icon = {"Geoapify": "🌐", "OpenStreetMap": "🗺️", "Foursquare": "📍", "Canvas Events": "🎪"}.get(source, "📌")

    with st.container(border=True):
        if image_url:
            try:
                st.image(image_url, use_container_width=True)
            except Exception:
                pass
        elif map_url:
            try:
                st.image(map_url, use_container_width=True)
            except Exception:
                pass

        st.markdown(f"#### {name}")
        st.caption(f"{src_icon} **{vtype}** · via {source}")

        if address:
            if image_url and map_url:
                col_addr, col_map = st.columns([3, 1])
                with col_addr:
                    st.markdown(f"📍 {address}")
                with col_map:
                    try:
                        st.image(map_url, use_container_width=True)
                    except Exception:
                        pass
            else:
                st.markdown(f"📍 {address}")

        if capacity:
            total = _parse_first_num(capacity)
            if total:
                seated = int(total * 0.65)
                standing = int(total * 1.15)
                fits = seated >= required_guests if required_guests else True
                cap_label = "✅ **Capacity:**" if fits else "⚠️ **Capacity:**"
                st.markdown(
                    f"{cap_label} {capacity}  \n"
                    f"  🪑 **Seated**: ~{seated:,} guests  \n"
                    f"  🧍 **Standing**: ~{standing:,} guests",
                )
                if required_guests and seated < required_guests:
                    st.warning(f"Seated estimate ({seated:,}) is below your {required_guests:,} guest requirement.")
            else:
                st.markdown(f"👥 **Capacity:** {capacity}")
        else:
            st.caption("👥 Capacity: not listed — contact venue to confirm")

        if desc:
            st.markdown(f"📝 *{desc[:300]}{'…' if len(desc) > 300 else ''}*")

        if ev_type_match:
            st.success("✅ Matches your event type")
        if ev_types:
            tags_html = " ".join(
                f'<span style="background:#1f4068;color:#e0e0e0;border-radius:4px;padding:2px 7px;margin:2px;font-size:0.78em;display:inline-block">{t}</span>'
                for t in ev_types[:10]
            )
            st.markdown(f"🏷️ {tags_html}", unsafe_allow_html=True)

        pricing_parts = []
        if price_day:
            pricing_parts.append(f"💷 **Day hire:** {price_day}")
        if price_hour:
            pricing_parts.append(f"⏱️ **Hourly:** {price_hour}")
        if price_range:
            pricing_parts.append(f"💰 **Price:** {price_range}")
        if min_spend:
            pricing_parts.append(f"📋 **Min spend:** {min_spend}")
        if pricing_parts:
            st.markdown("  \n".join(pricing_parts))

        facilities = []
        if wheelchair and wheelchair.lower() in ("yes", "designated", "limited"):
            facilities.append("♿ Wheelchair accessible")
        if internet and internet.lower() not in ("no", ""):
            facilities.append("📶 WiFi")
        if outdoor and outdoor.lower() == "yes":
            facilities.append("🌿 Outdoor area")
        if stars:
            facilities.append(f"⭐ {stars}-star")
        if rooms:
            facilities.append(f"🏨 {rooms} rooms")
        if facilities:
            st.markdown("**Facilities:** " + " · ".join(facilities))

        contact_parts = []
        if phone:
            contact_parts.append(f"📞 `{phone}`")
        if email:
            contact_parts.append(f"✉️ `{email}`")
        if website:
            contact_parts.append(f"[🌐 Website]({website})")
        if contact_parts:
            st.markdown("**Contact:** " + " | ".join(contact_parts))
        else:
            st.caption("No contact info — search venue name online")

        # ── Canvas-specific rich sections ──────────────────────────────────
        canvas_price_guide    = v.get("canvas_price_guide") or {}
        canvas_cap_detail     = v.get("canvas_capacity_detail") or {}
        canvas_spaces         = v.get("canvas_spaces") or []
        canvas_perfect_for    = v.get("canvas_perfect_for") or []
        canvas_features       = v.get("canvas_features") or {}

        has_canvas_rich = any([canvas_price_guide, canvas_cap_detail, canvas_spaces,
                                canvas_perfect_for, canvas_features])
        if has_canvas_rich:
            with st.expander("📋 Full Venue Details (Canvas Events)", expanded=False):

                # Price Guide
                if canvas_price_guide:
                    st.markdown("**💷 Price Guide**")
                    fp = canvas_price_guide.get("from_price")
                    if fp:
                        st.markdown(f"From: **{fp}**")
                    days = canvas_price_guide.get("days") or {}
                    if days:
                        day_md = " | ".join(
                            f"**{d}:** {'~~Closed~~' if p == 'Closed' else p}"
                            for d, p in days.items()
                        )
                        st.markdown(day_md)
                    rooms_data = canvas_price_guide.get("rooms") or []
                    if rooms_data:
                        rows = [
                            {"Room": r.get("name",""), "Session": r.get("session",""),
                             "Time": r.get("time",""), "Price": r.get("price","")}
                            for r in rooms_data
                        ]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    st.divider()

                # Capacity Breakdown
                if canvas_cap_detail:
                    st.markdown("**👥 Venue Capacity**")
                    cap_cols = st.columns(5)
                    labels = [("🧍 Standing", "standing"), ("🎭 Theatre", "theatre"),
                              ("🍽️ Cabaret", "cabaret"), ("🥗 Dining", "dining"), ("📐 Sq/ft", "sqft")]
                    for col, (label, key) in zip(cap_cols, labels):
                        val = canvas_cap_detail.get(key)
                        if val:
                            col.metric(label, f"{int(val):,}" if key != "sqft" else f"{val:,.0f}")
                    st.divider()

                # Perfect For
                if canvas_perfect_for:
                    st.markdown("**🎯 Perfect For**")
                    pf_html = " ".join(
                        f'<span style="background:#0d3349;color:#e0e0e0;border-radius:4px;padding:2px 8px;margin:2px;font-size:0.78em;display:inline-block">{t}</span>'
                        for t in canvas_perfect_for
                    )
                    st.markdown(pf_html, unsafe_allow_html=True)
                    st.divider()

                # Features & Restrictions
                if canvas_features:
                    st.markdown("**✅ Features & Restrictions**")
                    for cat_name, items in canvas_features.items():
                        st.markdown(f"*{cat_name}*")
                        st.markdown("  ".join(f"• {i}" for i in items))
                    st.divider()

                # Spaces Available
                if canvas_spaces:
                    st.markdown(f"**🏢 Spaces Available ({len(canvas_spaces)})**")
                    for space in canvas_spaces:
                        sname = space.get("name", "Space")
                        sprice = space.get("price_per_day", "")
                        scap = space.get("capacity") or {}
                        surl = space.get("url", "")
                        simg = space.get("image_url", "")
                        sc1, sc2 = st.columns([1, 2])
                        with sc1:
                            if simg:
                                try:
                                    st.image(simg, use_container_width=True)
                                except Exception:
                                    pass
                        with sc2:
                            link = f"[{sname}]({surl})" if surl else sname
                            st.markdown(f"**{link}**")
                            if sprice:
                                st.markdown(f"💷 {sprice}")
                            if scap:
                                cap_str = " · ".join(
                                    f"{k.title()}: {v}" for k, v in scap.items()
                                )
                                st.caption(cap_str)

        # ── Canvas Events enrichment button ────────────────────────────────
        is_canvas = source == "Canvas Events" and website and "canvas-events.co.uk" in website
        enriched_map = st.session_state.get("sp_enriched", {})
        if is_canvas:
            if v.get("_enriched"):
                filled = v.get("_enrich_fields_filled") or []
                st.caption(f"✅ Enriched from Canvas Events" + (f" — filled: {', '.join(filled)}" if filled else ""))
            else:
                if st.button("🔍 Load full details from Canvas", key=f"enrich_{idx}"):
                    with st.spinner("Fetching details from Canvas Events…"):
                        try:
                            enriched = get_client().enrich_venue(v)
                            if "sp_enriched" not in st.session_state:
                                st.session_state["sp_enriched"] = {}
                            st.session_state["sp_enriched"][idx] = enriched
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Enrichment failed: {exc}")

        with st.expander("🔍 Raw JSON response"):
            st.json(v)


def _score_venue(v: dict, required_guests: int, venue_hire_budget: float, required_facilities: list) -> tuple:
    """Return (score:int, matched_facilities:list) for ranking venues."""
    score = 0
    matched_facs = []

    # Capacity fit (+3 if fits, +1 if just has data)
    cap = _parse_first_num(v.get("capacity", ""))
    if cap > 0:
        if required_guests and cap >= required_guests:
            score += 3
        else:
            score += 1

    # Budget fit
    if venue_hire_budget > 0:
        if v.get("within_hire_budget"):
            score += 3  # confirmed within budget
        elif v.get("parsed_price") is None:
            score += 1  # price unknown — don't penalise

    # Contact info available
    if v.get("phone") or v.get("email") or v.get("website"):
        score += 1

    # Facilities match
    if required_facilities:
        all_feats = []
        for cat_items in (v.get("canvas_features") or {}).values():
            all_feats.extend(str(x).lower() for x in (cat_items or []))
        if v.get("wheelchair", "").lower() in ("yes", "designated", "limited"):
            all_feats.append("wheelchair accessible")
        if v.get("internet_access", "").lower() not in ("", "no"):
            all_feats.extend(["wi-fi", "wifi"])
        if v.get("outdoor_seating", "").lower() == "yes":
            all_feats.append("outdoor area")
        for fac in required_facilities:
            fac_l = fac.lower()
            if any(fac_l in feat or feat in fac_l for feat in all_feats):
                score += 1
                matched_facs.append(fac)

    return score, matched_facs


# ── Session state keys ────────────────────────────────────────────────────────
for key in ("sp_raw_text", "sp_requirements", "sp_venues", "sp_source_counts",
            "sp_indexed_collection", "sp_tool_source", "sp_tool_name", "sp_budget",
            "sp_radius_km_used", "sp_venue_hire_budget", "sp_budget_currency"):
    if key not in st.session_state:
        st.session_state[key] = None

if "sp_enriched" not in st.session_state:
    st.session_state["sp_enriched"] = {}   # idx (int) → enriched venue dict



# ── Helpers for auto-creating an event from extracted requirements ─────────────
import json as _json
from datetime import datetime as _dt, timedelta as _td

_CATEGORY_MAP = {
    "conference": "Conference", "seminar": "Conference", "meeting": "Conference",
    "workshop": "Conference", "training": "Conference", "agm": "Conference",
    "wedding": "Wedding", "birthday": "Birthday", "graduation": "Graduation",
    "concert": "Concert", "party": "Party", "gala": "Party",
    "corporate party": "Corporate", "corporate reception": "Corporate",
    "corporate": "Corporate", "networking": "Corporate", "product launch": "Corporate",
    "awards": "Corporate", "exhibition": "Other", "festival": "Other",
}

def _map_sp_category(event_type: str) -> str:
    et = (event_type or "").lower().strip()
    for kw, cat in _CATEGORY_MAP.items():
        if kw in et:
            return cat
    return "Other"

def _parse_sp_date(date_str: str):
    """Try to parse a free-form date string into a Python datetime. Falls back to 30 days out."""
    if not date_str:
        return _dt.now() + _td(days=30)
    for fmt in ("%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d, %Y",
                "%d %B %Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return _dt.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    # Try parsing just the year
    import re as _re
    yr = _re.search(r"\b(20\d{2})\b", date_str)
    return _dt(_int(yr.group(1)) if yr else _dt.now().year + 1, 1, 1)

def _int(x):
    try:
        return int(x)
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Load or Describe Your Event
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("## Step 1 — Your Event Brief")

# ── Fetch events that have a saved brief ──────────────────────────────────────
try:
    _all_events = client.list_events()
    _events_with_brief = [e for e in _all_events if e.get("brief_json")]
except Exception:
    _all_events = []
    _events_with_brief = []

input_method = st.radio(
    "How would you like to start?",
    ["📅 Load from saved event", "📝 New brief (file or paste)"],
    horizontal=True,
    key="sp_input_method",
)

if input_method.startswith("📅"):
    # ── Load from existing event ───────────────────────────────────────────────
    if not _events_with_brief:
        st.info(
            "No events have a planning brief yet. "
            "Go to **Events → My Events** and click **📝 Add AI Planning Brief** on any event, then come back here."
        )
    else:
        event_options = {
            f"{e['title']} ({e['category']} · {e['date_time'][:10]})": e
            for e in _events_with_brief
        }
        selected_label = st.selectbox("Select event", list(event_options.keys()), key="sp_event_select")
        selected_event = event_options[selected_label]

        # Preview the saved brief
        try:
            saved_brief = _json.loads(selected_event["brief_json"])
            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Event type", saved_brief.get("event_type", "—"))
            pc2.metric("Guests", saved_brief.get("guest_count", "—"))
            pc3.metric("Budget", saved_brief.get("budget", "—"))
            pc4.metric("City", saved_brief.get("city", "—"))
        except Exception:
            saved_brief = {}
            st.caption("Could not parse saved brief — click Load to try anyway.")

        if st.button("📥 Load This Event's Brief", type="primary", key="sp_load_from_event"):
            try:
                reqs = saved_brief if saved_brief else _json.loads(selected_event["brief_json"])
                st.session_state["sp_raw_text"] = f"[Loaded from event: {selected_event['title']}]"
                st.session_state["sp_requirements"] = reqs
                st.session_state["sp_venues"] = None
                st.session_state["sp_source_counts"] = None
                st.session_state["sp_indexed_collection"] = None
                st.success(f"✅ Loaded brief for **{selected_event['title']}** — review and edit below.")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to load brief: {exc}")

else:
    # ── New brief: file upload or paste ───────────────────────────────────────
    new_brief_method = st.radio(
        "Input method",
        ["📎 Upload file (PDF / DOCX / TXT)", "✏️ Type / paste requirements"],
        horizontal=True,
        key="sp_new_method",
    )
    raw_text = ""

    if new_brief_method.startswith("📎"):
        uploaded = st.file_uploader("Upload event brief", type=["pdf", "docx", "txt"])
        if uploaded:
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            file_bytes = uploaded.read()
            if ext == "txt":
                raw_text = file_bytes.decode("utf-8", errors="ignore")
            else:
                st.info(f"📄 **{uploaded.name}** ({len(file_bytes):,} bytes) — click Extract to process.")
                st.session_state["_sp_upload_bytes"] = file_bytes
                st.session_state["_sp_upload_name"] = uploaded.name
                raw_text = f"[FILE: {uploaded.name}]"
    else:
        raw_text = st.text_area(
            "Paste your event requirements",
            height=200,
            placeholder=(
                "e.g. We need a venue near Camden Market for 250 guests on 18 Sep 2026. "
                "Budget £32,500. Looking for a conference or event hall within 1 km."
            ),
        )

    extract_btn = st.button(
        "✨ Extract Requirements with AI",
        type="primary",
        disabled=not raw_text.strip(),
    )

    if extract_btn and raw_text.strip():
        actual_text = raw_text
        if raw_text.startswith("[FILE:") and st.session_state.get("_sp_upload_bytes"):
            try:
                with st.spinner("Extracting text from file…"):
                    actual_text = client.extract_text_from_file(
                        st.session_state["_sp_upload_bytes"],
                        st.session_state["_sp_upload_name"],
                    )
                if not actual_text.strip():
                    st.error("No readable text found in the uploaded file.")
                    st.stop()
            except APIError as exc:
                st.error(f"Text extraction failed: {exc.detail}")
                st.stop()

        with st.spinner("AI is reading your brief…"):
            try:
                reqs = client.ai_extract_requirements(actual_text)
                st.session_state["sp_raw_text"] = actual_text
                st.session_state["sp_requirements"] = reqs
                st.session_state["sp_venues"] = None
                st.session_state["sp_source_counts"] = None
                st.session_state["sp_indexed_collection"] = None
            except APIError as exc:
                st.error(f"Extraction failed: {exc.detail}")
                st.stop()

        # ── Auto-create event and store brief in DB ───────────────────────────
        reqs = st.session_state.get("sp_requirements") or {}
        if reqs:
            try:
                ev_title    = reqs.get("event_name") or "Untitled Event"
                ev_date     = _parse_sp_date(reqs.get("event_date") or "")
                ev_location = reqs.get("location_hint") or reqs.get("city") or ""
                ev_category = _map_sp_category(reqs.get("event_type") or "")
                ev_desc_parts = []
                if reqs.get("guest_count"):
                    ev_desc_parts.append(f"Guests: {reqs['guest_count']}")
                if reqs.get("budget"):
                    ev_desc_parts.append(f"Budget: {reqs['budget']}")
                if reqs.get("event_type"):
                    ev_desc_parts.append(f"Type: {reqs['event_type']}")
                ev_desc = " · ".join(ev_desc_parts) if ev_desc_parts else None

                with st.spinner(f"Creating event **{ev_title}** in database…"):
                    new_event = client.create_event(
                        title=ev_title,
                        date_time=ev_date.isoformat(),
                        description=ev_desc,
                        location=ev_location or None,
                        category=ev_category,
                        is_shared=False,
                    )
                    client.save_event_brief(new_event["id"], actual_text)
                    st.session_state["sp_created_event_id"] = new_event["id"]
                st.success(
                    f"✅ Event **{ev_title}** created and brief saved — "
                    f"view it in **Events → My Events** (ID {new_event['id']})"
                )
            except Exception as exc:
                st.warning(f"Brief extracted but event creation failed: {exc} — you can continue planning below.")


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
    )

    collection_slug = st.text_input(
        "Collection name (slug)",
        value=(reqs.get("collection_slug", "my_event") or "my_event")[:20],
        max_chars=20,
    )

    default_facs = reqs.get("required_facilities") or st.session_state.get("sp_req_facilities") or []
    required_facilities_input = st.multiselect(
        "Required Facilities (optional — used to rank venues)",
        options=COMMON_FACILITIES,
        default=[f for f in default_facs if f in COMMON_FACILITIES],
        key="sp_req_facilities",
        help="Venues that have these facilities will rank higher in the Top 10",
    )

    st.markdown("---")

    # Show venue hire budget preview
    _currency_prev, _amount_prev = _parse_budget_amount(budget)
    if _amount_prev > 0:
        venue_hire_preview = _amount_prev * 0.35
        st.info(
            f"💡 **Venue hire budget:** {_currency_prev}{venue_hire_preview:,.0f} "
            f"(35% of {_currency_prev}{_amount_prev:,} total — from budget planner). "
            "Only venues within this range will be shown first; radius auto-expands until at least one is found."
        )

    fetch_btn = st.button(
        f"🔍 Fetch Venues near **{location_hint or city}** (radius {radius_km} km)",
        type="primary",
        disabled=not city.strip() or not selected_cats,
    )

    if fetch_btn:
        _currency, _amount = _parse_budget_amount(budget)
        venue_hire_budget = round(_amount * 0.35) if _amount > 0 else 0

        with st.spinner(f"Fetching venues within budget{' (auto-expanding radius until within-budget venue found)' if venue_hire_budget else ''}…"):
            try:
                result = client.search_venues(
                    city=location_hint.strip() or city.strip(),
                    categories=selected_cats,
                    radius_km=radius_km,
                    use_foursquare=True,
                    use_geoapify=True,
                    enrich_details=True,
                    max_venues=500,
                    event_type=event_type,
                    max_radius_km=25,
                    venue_hire_budget=venue_hire_budget,
                )
                st.session_state["sp_venues"] = result["venues"]
                st.session_state["sp_source_counts"] = result["source_counts"]
                st.session_state["sp_tool_source"] = result.get("tool_source", "local")
                st.session_state["sp_tool_name"] = result.get("tool_name", "venue_service")
                st.session_state["sp_radius_km_used"] = result.get("radius_km_used", radius_km)
                st.session_state["sp_venue_hire_budget"] = venue_hire_budget
                st.session_state["sp_budget_currency"] = _currency or "£"
                st.session_state["sp_enriched"] = {}
                st.session_state["sp_requirements"].update({
                    "event_name": event_name, "city": city, "location_hint": location_hint,
                    "radius_km": radius_km, "guest_count": guest_count, "budget": budget,
                    "event_date": event_date, "event_type": event_type,
                    "categories": selected_cats, "collection_slug": collection_slug,
                    "required_facilities": required_facilities_input,
                })
                if _amount > 0:
                    try:
                        st.session_state["sp_budget"] = client.budget_planner(
                            event_type, _amount, _currency or "GBP"
                        )
                    except Exception:
                        st.session_state["sp_budget"] = None
            except APIError as exc:
                st.error(f"Error fetching venues: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Preview venues & index
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state["sp_venues"] is not None:
    venues = st.session_state["sp_venues"]
    counts = st.session_state["sp_source_counts"] or {}
    reqs = st.session_state["sp_requirements"] or {}
    ev_type = reqs.get("event_type", "")

    st.markdown("---")
    st.markdown("## Step 3 — Preview Venues & Index")
    tool_badge(
        st.session_state.get("sp_tool_source") or "local",
        st.session_state.get("sp_tool_name") or "venue_service",
    )
    data_source_badges(counts)

    radius_km_used = st.session_state.get("sp_radius_km_used") or reqs.get("radius_km", 5)
    requested_radius = reqs.get("radius_km", 5)

    venue_hire_budget = st.session_state.get("sp_venue_hire_budget", 0)
    budget_currency = st.session_state.get("sp_budget_currency", "£")

    if not venues:
        st.error(
            f"🔍 **No data found** — no venues found within {radius_km_used} km"
            + (f" matching your venue hire budget of {budget_currency}{venue_hire_budget:,.0f}" if venue_hire_budget else "")
            + ". Try expanding your search radius, adjusting your budget, or adding more venue categories."
        )
    else:
        # ── Budget-based venue split ───────────────────────────────────────────
        if venue_hire_budget > 0:
            within_budget = [v for v in venues if v.get("within_hire_budget")]
            no_price      = [v for v in venues if not v.get("within_hire_budget") and not v.get("over_hire_budget")]
            over_budget   = [v for v in venues if v.get("over_hire_budget")]
        else:
            within_budget, no_price, over_budget = [], venues, []

        # ── Top-level metrics ──────────────────────────────────────────────────
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Total venues", len(venues))
        if radius_km_used and radius_km_used != requested_radius:
            col_b.metric("Radius used", f"{radius_km_used} km", delta=f"+{radius_km_used - requested_radius} km")
        else:
            col_b.metric("Radius used", f"{radius_km_used} km")
        if venue_hire_budget > 0:
            col_c.metric("✅ Within budget", len(within_budget))
            col_d.metric("❌ Over budget", len(over_budget))
        else:
            with_cap = sum(1 for v in venues if v.get("capacity"))
            with_contact = sum(1 for v in venues if v.get("phone") or v.get("email") or v.get("website"))
            col_c.metric("With capacity data", with_cap)
            col_d.metric("With contact info", with_contact)

        if venue_hire_budget > 0:
            st.success(
                f"💷 Venue hire budget: **{budget_currency}{venue_hire_budget:,.0f}** (35% of total) · "
                f"**{len(within_budget)}** venues within budget · "
                f"**{len(no_price)}** price unknown · "
                f"**{len(over_budget)}** over budget (hidden below)"
            )
            if not within_budget and not no_price:
                st.warning(
                    f"No venues found within your venue hire budget of {budget_currency}{venue_hire_budget:,.0f}. "
                    "All fetched venues exceed this budget — showing them below for reference."
                )

        st.download_button(
            label="⬇️ Download venues as JSON",
            data=json.dumps(venues, indent=2, ensure_ascii=False),
            file_name=f"venues_{(reqs.get('city') or 'export').lower().replace(' ', '_')}.json",
            mime="application/json",
        )

        st.markdown("---")

        # ── Parse guest count & budget ─────────────────────────────────────────
        try:
            required_guests = int(str(reqs.get("guest_count") or "").replace(",", "")) or None
        except (ValueError, TypeError):
            required_guests = None

        budget_str = reqs.get("budget") or ""

        # ── Budget split table ─────────────────────────────────────────────────
        currency, budget_amount = _parse_budget_amount(budget_str)
        budget_data = st.session_state.get("sp_budget")
        if budget_amount > 0 and budget_data and budget_data.get("breakdown"):
            with st.expander(f"💰 Suggested Budget Split — {budget_str} total", expanded=True):
                tool_badge(budget_data.get("tool_source", "local"), budget_data.get("tool_name", "local_budget_planner"))
                rows = []
                for item in budget_data["breakdown"]:
                    row = {
                        "Category": item["category"],
                        "% of Budget": item["percentage"],
                        f"Amount ({budget_data.get('currency', currency)})": item["display"],
                    }
                    if "Venue" in item["category"]:
                        row["Category"] = f"🏛️ {item['category']} ← your search limit"
                    rows.append(row)
                st.dataframe(rows, use_container_width=True, hide_index=True)
                if budget_data.get("note"):
                    st.caption(budget_data["note"])
            st.markdown("---")

        # ── Capacity filter ────────────────────────────────────────────────────
        # Determine display pool: budget-within + no-price (+ over-budget if nothing else)
        if venue_hire_budget > 0:
            pool = within_budget + no_price
            if not pool:
                pool = over_budget  # nothing better — show all with a note
        else:
            pool = venues

        if required_guests:
            suitable, unknown_cap, too_small = [], [], []
            for v in pool:
                total = _parse_first_num(v.get("capacity"))
                if total == 0:
                    unknown_cap.append(v)
                elif total >= required_guests:
                    suitable.append(v)
                else:
                    too_small.append(v)
            display_venues = suitable + unknown_cap
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("✅ Fits guest count", len(suitable))
            fc2.metric("❓ Capacity unknown", len(unknown_cap))
            fc3.metric("❌ Too small (hidden)", len(too_small))
        else:
            display_venues = pool

        if not display_venues:
            st.warning("🔍 **No data found** matching all your filters. Try relaxing budget or guest count constraints.")

        st.markdown("---")

        # ── Top 10 Best Matches ────────────────────────────────────────────────
        if display_venues:
            required_facilities = reqs.get("required_facilities") or st.session_state.get("sp_req_facilities") or []

            # Score every venue then take top 10
            scored = []
            for v in display_venues:
                sc, mf = _score_venue(v, required_guests or 0, venue_hire_budget, required_facilities)
                scored.append((sc, mf, v))
            scored.sort(key=lambda x: x[0], reverse=True)
            top10 = scored[:10]

            fac_hint = (f" · facilities: {', '.join(required_facilities)}" if required_facilities else "")
            st.markdown(
                f"### 🏆 Top 10 Best Matches"
                f"<br><small style='color:grey'>Ranked by: capacity fit · budget · contact info{fac_hint}</small>",
                unsafe_allow_html=True,
            )

            for rank, (sc, mf, v) in enumerate(top10, 1):
                cap_num = _parse_first_num(v.get("capacity", ""))
                cap_ok  = cap_num >= (required_guests or 1) if cap_num > 0 else None
                bud_ok  = v.get("within_hire_budget")
                bud_unknown = v.get("parsed_price") is None and venue_hire_budget > 0

                # Status dot
                if cap_ok and (bud_ok or not venue_hire_budget):
                    dot = "🟢"
                elif cap_ok or bud_ok:
                    dot = "🟡"
                else:
                    dot = "🔵"

                with st.container(border=True):
                    col_rk, col_info = st.columns([1, 9])
                    col_rk.markdown(
                        f"<div style='font-size:2rem;font-weight:bold;text-align:center;padding-top:6px'>"
                        f"#{rank}</div><div style='text-align:center'>{dot}</div>",
                        unsafe_allow_html=True,
                    )
                    with col_info:
                        name_line = f"**{v.get('name', 'Unknown Venue')}**"
                        addr = v.get("address") or ""
                        if addr:
                            name_line += f"  ·  📍 {addr[:80]}"
                        st.markdown(name_line)

                        tags = []
                        if v.get("capacity"):
                            tags.append(f"👥 {v['capacity']}" + (" ✅" if cap_ok else (" ⚠️ too small" if cap_ok is False else "")))
                        # Price: use parsed_price or canvas_price_guide
                        from_price = (v.get("canvas_price_guide") or {}).get("from_price")
                        if v.get("parsed_price"):
                            price_lbl = f"💷 £{v['parsed_price']:,.0f}/day"
                            price_lbl += " ✅ within budget" if bud_ok else (" ❌ over budget" if v.get("over_hire_budget") else "")
                            tags.append(price_lbl)
                        elif from_price:
                            tags.append(f"💷 {from_price}" + (" ✅" if bud_ok else ("" if bud_unknown else "")))
                        elif venue_hire_budget > 0:
                            tags.append("💷 Price: contact venue")
                        if mf:
                            tags.append(f"🎯 {', '.join(mf)}")
                        if v.get("event_type_match"):
                            tags.append("✅ Matches event type")
                        st.markdown("  ·  ".join(tags) if tags else "📋 Contact venue for full details")

                        ct_parts = []
                        if v.get("website"):
                            ct_parts.append(f"[🌐 Website]({v['website']})")
                        if v.get("phone"):
                            ct_parts.append(f"📞 `{v['phone']}`")
                        if v.get("email"):
                            ct_parts.append(f"✉️ `{v['email']}`")
                        if ct_parts:
                            st.markdown("  |  ".join(ct_parts))

            st.markdown("---")

            # ── All matching venues (paginated) ────────────────────────────────
            with st.expander(f"📋 All {len(display_venues)} matching venues", expanded=False):
                CARDS_PER_PAGE = 12
                if "sp_venue_page" not in st.session_state:
                    st.session_state["sp_venue_page"] = 0

                total_pages = max(1, (len(display_venues) - 1) // CARDS_PER_PAGE + 1)
                current_page = min(st.session_state["sp_venue_page"], total_pages - 1)

                pg_c1, pg_c2, pg_c3 = st.columns([1, 4, 1])
                if pg_c1.button("◀ Prev", disabled=current_page == 0, key="prev_page"):
                    st.session_state["sp_venue_page"] = current_page - 1
                    st.rerun()
                pg_c2.markdown(
                    f"<div style='text-align:center;padding-top:8px;'>Showing "
                    f"<b>{current_page * CARDS_PER_PAGE + 1}–{min((current_page + 1) * CARDS_PER_PAGE, len(display_venues))}</b> "
                    f"of <b>{len(display_venues)}</b> · Page {current_page + 1}/{total_pages}</div>",
                    unsafe_allow_html=True,
                )
                if pg_c3.button("Next ▶", disabled=current_page >= total_pages - 1, key="next_page"):
                    st.session_state["sp_venue_page"] = current_page + 1
                    st.rerun()

                page_venues = display_venues[
                    current_page * CARDS_PER_PAGE:(current_page + 1) * CARDS_PER_PAGE
                ]
                page_offset = current_page * CARDS_PER_PAGE
                for i in range(0, len(page_venues), 2):
                    c1, c2 = st.columns(2)
                    with c1:
                        _render_venue_card(page_venues[i], idx=page_offset + i, required_guests=required_guests or 0)
                    if i + 1 < len(page_venues):
                        with c2:
                            _render_venue_card(page_venues[i + 1], idx=page_offset + i + 1, required_guests=required_guests or 0)

        # ── Index buttons ──────────────────────────────────────────────────────
        st.markdown("---")
        slug = reqs.get("collection_slug", "my_event") or "my_event"
        event_name = reqs.get("event_name", "Event Plan")
        city = reqs.get("city", reqs.get("location_hint", ""))
        ev_type_for_index = reqs.get("event_type", "") or ""

        idx_col1, idx_col2 = st.columns(2)

        with idx_col1:
            st.markdown(f"**Event plan collection:** `evp_u{st.session_state['user_id']}_{slug[:20]}`")
            if st.button(f"⚡ Index {len(venues)} venues + event brief", type="primary", key="idx_plan"):
                with st.spinner("Embedding and indexing everything…"):
                    try:
                        client.index_event_plan(
                            event_name=event_name,
                            collection_slug=slug,
                            document_text=st.session_state["sp_raw_text"] or "",
                            venues=venues,
                            city=city,
                        )
                        st.session_state["sp_indexed_collection"] = slug
                        st.success(
                            f"✅ **{event_name}** indexed! "
                            f"{len(venues)} venues + brief are now searchable."
                        )
                    except APIError as exc:
                        st.error(f"Indexing failed: {exc.detail}")

        with idx_col2:
            et_slug = ev_type_for_index.lower().replace(" ", "_")[:20] if ev_type_for_index else "general"
            uid = st.session_state.get("user_id", 0)
            st.markdown(f"**Event-type collection:** `evt_u{uid}_{et_slug}`")
            label = ev_type_for_index.title() if ev_type_for_index else "General"
            if st.button(
                f"🎯 Index to **{label}** event-type collection",
                key="idx_event_type",
                help="Stores rich text + raw JSON chunks per venue, grouped by event type",
            ):
                with st.spinner(f"Indexing {len(venues)} venues into '{label}' collection…"):
                    try:
                        result = client.index_event_type_venues(
                            event_type=ev_type_for_index or "general",
                            city=city,
                            venues=venues,
                        )
                        st.success(
                            f"✅ **{len(venues)} venues** indexed into `evt_u{uid}_{et_slug}` — "
                            f"visible in **Data Indexing → By Event Type**."
                        )
                    except APIError as exc:
                        st.error(f"Indexing failed: {exc.detail}")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Inline chat
# ═══════════════════════════════════════════════════════════════════════════════
if st.session_state.get("sp_indexed_collection"):
    st.markdown("---")
    st.markdown("## Step 4 — Chat with Your Event Data")

    if "sp_chat_history" not in st.session_state:
        st.session_state["sp_chat_history"] = []

    reqs = st.session_state["sp_requirements"] or {}
    event_name = reqs.get("event_name", "Event Plan")
    city = reqs.get("city", "")
    collection_slug = st.session_state["sp_indexed_collection"]
    user_id = st.session_state["user_id"]
    collection_name = f"evp_u{user_id}_{collection_slug[:20]}"

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
                history = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state["sp_chat_history"][:-1]
                ]

                answer_data = None
                validated = {}

                with st.status("🔄 Agent working…", expanded=True) as status:
                    st.write("**🛡️ Input Guardrail** — abuse · spelling · sarcasm")
                    guard_in = client.ai_check_input(prompt)

                    if not guard_in["allowed"]:
                        status.update(label="🚫 Blocked", state="error", expanded=True)
                        abuse_msg = "I'm here to help with event planning. Please keep the conversation respectful."
                        st.error(f"Blocked: {guard_in['rejection_reason']}")
                        st.session_state["sp_chat_history"].append({"role": "assistant", "content": abuse_msg})
                        st.stop()

                    st.write(f"  ✅ Category: `{guard_in['category']}`")
                    if guard_in.get("was_corrected"):
                        for c in guard_in.get("corrections", []):
                            st.write(f"  ✏️ `{c['original']}` → `{c['corrected']}`")
                    if guard_in.get("is_sarcastic"):
                        st.write(f"  😏 Sarcasm — real intent: *{guard_in['real_intent']}*")

                    effective_query = guard_in["real_intent"]
                    st.divider()

                    st.write(f"**🤖 Event Planning Agent** · Collection: `{collection_name}`")
                    st.caption("Agent decides which tools to call based on your question.")

                    _TOOL_ICONS = {
                        "rag_search": "🔍",
                        "filter_by_capacity": "📐",
                        "search_venues_live": "🌐",
                        "find_catering_options": "🍽️",
                    }

                    trace_events = client.ai_run_agent(
                        query=effective_query,
                        collection_name=collection_name,
                        city=city,
                        chat_history=history,
                    )

                    for event in trace_events:
                        etype = event.get("type")
                        if etype == "thinking":
                            st.caption(f"  💭 {event.get('message', '')}")
                        elif etype == "tool_call":
                            tool = event.get("tool", "")
                            args = event.get("args", {})
                            icon = _TOOL_ICONS.get(tool, "🔧")
                            arg_str = " · ".join(
                                f"`{k}={v}`" for k, v in args.items()
                                if k not in ("collection_name",)
                            )
                            st.write(f"  {icon} **Tool called:** `{tool}` — {arg_str}")
                        elif etype == "tool_result":
                            st.write(f"    ↳ Result: {event.get('summary', '')}")
                        elif etype == "answer":
                            answer_data = event.get("data", {})
                        elif etype == "error":
                            st.error(event.get("message", "Unknown error"))

                    # Output guardrail — validate schema
                    st.divider()
                    st.write("**🛡️ Output Guardrail** — JSON schema validation")
                    if answer_data:
                        answer_data.setdefault("answer", "No answer generated.")
                        answer_data.setdefault("sources_used", [])
                        answer_data.setdefault("confidence", "low")
                        answer_data.setdefault("query_interpretation", "")
                        if answer_data["confidence"] not in ("high", "medium", "low"):
                            answer_data["confidence"] = "low"
                        validated = answer_data
                    else:
                        validated = {"answer": "No answer was generated.", "sources_used": [], "confidence": "low", "query_interpretation": ""}

                    confidence_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(validated["confidence"], "⚪")
                    tools_used = answer_data.get("tools_used", []) if answer_data else []
                    st.write(
                        f"  ✅ Schema valid · "
                        f"Confidence: {confidence_icon} `{validated['confidence']}` · "
                        f"Tools: `{len(tools_used)}`"
                    )
                    st.caption(f"Interpretation: *{validated.get('query_interpretation', '')}*")
                    status.update(label="✅ Agent finished — answer ready", state="complete", expanded=False)

                st.markdown(validated["answer"])
                cols = st.columns(3)
                cols[0].caption(f"{confidence_icon} Confidence: **{validated['confidence']}**")
                if validated.get("sources_used"):
                    cols[1].caption("Sources: " + ", ".join(f"*{s}*" for s in validated["sources_used"][:3]))
                if tools_used:
                    cols[2].caption("Tools: " + " · ".join(f"`{t}`" for t in set(tools_used)))

                st.session_state["sp_chat_history"].append(
                    {"role": "assistant", "content": validated["answer"]}
                )

            except APIError as exc:
                err = f"API Error ({exc.status_code}): {exc.detail}"
                st.error(err)
                st.session_state["sp_chat_history"].append({"role": "assistant", "content": err})
            except Exception as exc:
                import traceback
                err = f"Error: {exc}"
                st.error(err)
                st.caption(traceback.format_exc())
                st.session_state["sp_chat_history"].append({"role": "assistant", "content": err})

    if st.session_state["sp_chat_history"]:
        if st.button("🗑️ Clear chat"):
            st.session_state["sp_chat_history"] = []
            st.rerun()
