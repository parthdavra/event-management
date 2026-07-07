import json
import streamlit as st
from datetime import datetime, date, time

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="Events — AI Event Manager", page_icon="📅", layout="wide")
require_auth()
show_sidebar()

st.title("📅 Events")

client = get_client()

CATEGORIES = ["Conference", "Wedding", "Birthday", "Graduation", "Concert", "Party", "Corporate", "Other"]


def _brief_summary(brief_json: str) -> str:
    try:
        b = json.loads(brief_json)
        parts = []
        if b.get("event_type"):
            parts.append(b["event_type"].title())
        if b.get("guest_count"):
            parts.append(f"{b['guest_count']} guests")
        if b.get("budget"):
            parts.append(b["budget"])
        if b.get("city"):
            parts.append(b["city"])
        return " · ".join(parts) if parts else "Brief saved"
    except Exception:
        return "Brief saved"


def _catering_summary(catering_json: str) -> str:
    try:
        c = json.loads(catering_json)
        groups = c.get("groups") or []
        total = c.get("total_headcount") or sum(g.get("count", 0) for g in groups)
        budget = c.get("budget")
        loc = c.get("location")
        parts = []
        if groups:
            parts.append(f"{len(groups)} groups")
        if total:
            parts.append(f"{total} total guests")
        if budget:
            parts.append(f"£{budget:,.0f}" if isinstance(budget, (int, float)) else str(budget))
        if loc:
            parts.append(loc)
        return " · ".join(parts) if parts else "Catering brief saved"
    except Exception:
        return "Catering brief saved"


tab_mine, tab_shared, tab_create = st.tabs(["My Events", "Shared Events", "Create Event"])

# ── Create Event ──────────────────────────────────────────────────────────────
with tab_create:
    st.subheader("Create a New Event")

    with st.form("create_event_form"):
        title = st.text_input("Event Title *", placeholder="e.g., Annual Team Meeting")
        col1, col2 = st.columns(2)
        with col1:
            event_date = st.date_input("Date *", min_value=date.today())
            category = st.selectbox("Category", CATEGORIES)
        with col2:
            event_time = st.time_input("Time *", value=time(9, 0))
            is_shared = st.checkbox("Share with other users")
        location = st.text_input("Location", placeholder="e.g., London, UK")
        description = st.text_area("Description", placeholder="Event details…", height=80)

        st.markdown("---")
        st.markdown("**📝 Planning Brief** *(optional)*")
        brief_file = st.file_uploader(
            "Upload brief file (PDF / DOCX / TXT) — content used as planning brief",
            type=["pdf", "docx", "txt"],
            key="create_brief_file",
        )
        brief_text = st.text_area(
            "Or paste / type planning brief here",
            height=100,
            key="create_brief_text",
            placeholder="e.g. Corporate conference for 250 guests in Camden, budget £32,500, date 22 Jul 2026, AV equipment needed",
        )

        st.markdown("**🍱 Catering Requirements** *(optional)*")
        catering_text = st.text_area(
            "Catering requirements",
            height=80,
            key="create_catering_text",
            placeholder="e.g. 200 guests: 50 vegan, 100 halal non-veg, 50 vegetarian, budget £3,000, London",
        )

        create_submitted = st.form_submit_button("Create Event", type="primary", use_container_width=True)

    if create_submitted:
        if not title:
            st.warning("Event title is required.")
        else:
            try:
                dt_str = datetime.combine(event_date, event_time).isoformat()
                new_event = client.create_event(
                    title=title,
                    date_time=dt_str,
                    description=description or None,
                    location=location or None,
                    category=category,
                    is_shared=is_shared,
                )

                # Extract file text once — used for BOTH planning brief and catering
                file_text = ""
                if brief_file is not None:
                    try:
                        with st.spinner("Extracting text from uploaded file…"):
                            file_text = client.extract_text_from_file(brief_file.read(), brief_file.name)
                    except APIError as exc:
                        st.warning(f"File extraction failed ({exc.detail}) — using text box content only.")

                # Planning brief = file text + brief text box (combined)
                actual_brief = (
                    (file_text + ("\n\n" + brief_text if brief_text.strip() else "")).strip()
                    if file_text else brief_text.strip()
                )
                # Catering = file text + catering text box (combined)
                actual_catering = (
                    (file_text + ("\n\n" + catering_text if catering_text.strip() else "")).strip()
                    if file_text else catering_text.strip()
                )

                if actual_brief:
                    with st.spinner("AI is extracting planning requirements…"):
                        try:
                            client.save_event_brief(new_event["id"], actual_brief)
                        except APIError as exc:
                            st.warning(f"Brief extraction failed: {exc.detail}")

                if actual_catering:
                    with st.spinner("AI is extracting catering requirements…"):
                        try:
                            client.save_event_catering_brief(new_event["id"], actual_catering)
                        except APIError as exc:
                            st.warning(f"Catering extraction failed: {exc.detail}")

                badges = (" 📝" if actual_brief else "") + (" 🍱" if actual_catering else "")
                st.success(f"✅ Event **{title}** created!{badges} Go to **My Events** to view or update.")
                st.rerun()
            except APIError as exc:
                st.error(f"Error: {exc.detail}")

# ── My Events ─────────────────────────────────────────────────────────────────
with tab_mine:
    col_filter, col_sort = st.columns(2)
    with col_filter:
        cat_filter = st.selectbox("Filter by Category", ["All"] + CATEGORIES, key="filter_cat")
    with col_sort:
        sort_by = st.selectbox(
            "Sort by",
            ["date_time", "title", "created_at"],
            format_func=lambda x: {"date_time": "Date", "title": "Title", "created_at": "Created"}[x],
            key="sort_events",
        )

    try:
        events = client.list_events(
            category=cat_filter if cat_filter != "All" else None,
            sort_by=sort_by,
        )
    except APIError as exc:
        st.error(f"Could not load events: {exc.detail}")
        events = []

    if not events:
        st.info("No events yet. Create your first event in the **Create Event** tab!")
    else:
        st.markdown(f"**{len(events)} event(s)**")

        if "edit_event_id" not in st.session_state:
            st.session_state["edit_event_id"] = None

        for event in events:
            dt = datetime.fromisoformat(event["date_time"].replace("Z", "+00:00")) if isinstance(event["date_time"], str) else event["date_time"]
            has_brief = bool(event.get("brief_json"))
            has_catering = bool(event.get("catering_json"))
            brief_badge = " 📝" if has_brief else ""
            catering_badge = " 🍱" if has_catering else ""
            header = (
                f"{'🌐' if event['is_shared'] else '🔒'} **{event['title']}**{brief_badge}{catering_badge} "
                f"— {dt.strftime('%d %b %Y, %H:%M')} | {event['category']}"
            )

            with st.expander(header):
                if event["id"] == st.session_state["edit_event_id"]:
                    # ── Edit form ──────────────────────────────────────────────
                    with st.form(f"edit_form_{event['id']}"):
                        new_title = st.text_input("Title", value=event["title"])
                        col1, col2 = st.columns(2)
                        with col1:
                            new_date = st.date_input("Date", value=dt.date())
                            new_cat = st.selectbox(
                                "Category", CATEGORIES,
                                index=CATEGORIES.index(event["category"]) if event["category"] in CATEGORIES else 0,
                            )
                        with col2:
                            new_time = st.time_input("Time", value=dt.time())
                            new_shared = st.checkbox("Shared", value=event["is_shared"])
                        new_loc = st.text_input("Location", value=event["location"] or "")
                        new_desc = st.text_area("Description", value=event["description"] or "")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            save = st.form_submit_button("Save", type="primary", use_container_width=True)
                        with col_cancel:
                            cancel = st.form_submit_button("Cancel", use_container_width=True)

                    if save:
                        try:
                            new_dt_str = datetime.combine(new_date, new_time).isoformat()
                            client.update_event(
                                event["id"],
                                title=new_title,
                                date_time=new_dt_str,
                                category=new_cat,
                                location=new_loc or None,
                                description=new_desc or None,
                                is_shared=new_shared,
                            )
                            st.session_state["edit_event_id"] = None
                            st.rerun()
                        except APIError as exc:
                            st.error(exc.detail)
                    if cancel:
                        st.session_state["edit_event_id"] = None
                        st.rerun()

                else:
                    # ── View mode ──────────────────────────────────────────────
                    if event["description"]:
                        st.markdown(event["description"])

                    info_col, action_col = st.columns([3, 1])
                    with info_col:
                        if event["location"]:
                            st.markdown(f"📍 **Location:** {event['location']}")
                        st.markdown(f"🏷️ **Category:** {event['category']}")
                        st.markdown(f"{'🌐 Shared' if event['is_shared'] else '🔒 Private'}")

                    with action_col:
                        col_edit, col_del = st.columns(2)
                        with col_edit:
                            if st.button("✏️", key=f"edit_{event['id']}", help="Edit event"):
                                st.session_state["edit_event_id"] = event["id"]
                                st.rerun()
                        with col_del:
                            if st.button("🗑️", key=f"del_{event['id']}", help="Delete event"):
                                st.session_state[f"confirm_del_{event['id']}"] = True
                                st.rerun()

                    if st.session_state.get(f"confirm_del_{event['id']}"):
                        st.warning(f"Delete **{event['title']}**? This cannot be undone.")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Yes, Delete", key=f"yes_{event['id']}", type="primary"):
                                try:
                                    client.delete_event(event["id"])
                                    st.session_state.pop(f"confirm_del_{event['id']}", None)
                                    st.rerun()
                                except APIError as exc:
                                    st.error(exc.detail)
                        with c2:
                            if st.button("Cancel", key=f"no_{event['id']}"):
                                st.session_state.pop(f"confirm_del_{event['id']}", None)
                                st.rerun()

                    st.markdown("---")

                    # ── AI Planning Brief section ──────────────────────────────
                    brief_label = (
                        f"📝 AI Planning Brief — {_brief_summary(event['brief_json'])}"
                        if has_brief else "📝 AI Planning Brief"
                    )
                    with st.expander(brief_label, expanded=not has_brief):
                        if has_brief:
                            try:
                                saved = json.loads(event["brief_json"])
                                bcol1, bcol2 = st.columns(2)
                                with bcol1:
                                    st.markdown(f"**Event type:** {saved.get('event_type','—')}")
                                    st.markdown(f"**City:** {saved.get('city','—')}")
                                    st.markdown(f"**Location hint:** {saved.get('location_hint','—')}")
                                    st.markdown(f"**Guest count:** {saved.get('guest_count','—')}")
                                with bcol2:
                                    st.markdown(f"**Budget:** {saved.get('budget','—')}")
                                    st.markdown(f"**Date:** {saved.get('event_date','—')}")
                                    st.markdown(f"**Radius:** {saved.get('radius_km','—')} km")
                                    cats = ", ".join(saved.get("categories") or []) or "—"
                                    st.markdown(f"**Categories:** {cats}")
                                st.caption("Use the Smart Planner to fetch venues using this brief →")
                            except Exception:
                                st.warning("Brief data is malformed — please re-enter below.")
                            st.markdown("---")

                        st.markdown("**Update planning brief:**" if has_brief else "**Add planning brief to this event:**")
                        st.caption("Paste your planning brief as text. To upload a file, use the **Create Event** tab.")
                        update_brief = st.text_area(
                            "Planning brief text",
                            height=100,
                            key=f"update_brief_{event['id']}",
                            placeholder="e.g. Corporate conference for 250 guests in Camden, budget £32,500, date 22 Jul 2026",
                        )
                        if st.button("✨ Extract & Save Brief", key=f"save_brief_{event['id']}", type="primary"):
                            if not update_brief.strip():
                                st.warning("Please paste some planning brief text first.")
                            else:
                                with st.spinner("AI is extracting requirements…"):
                                    try:
                                        client.save_event_brief(event["id"], update_brief)
                                        st.success("✅ Planning brief saved!")
                                        st.rerun()
                                    except APIError as exc:
                                        st.error(f"Failed: {exc.detail}")

                    # ── Catering Requirements section ──────────────────────────
                    cater_label = (
                        f"🍱 Catering Requirements — {_catering_summary(event['catering_json'])}"
                        if has_catering else "🍱 Catering Requirements"
                    )
                    with st.expander(cater_label, expanded=False):
                        if has_catering:
                            try:
                                saved_c = json.loads(event["catering_json"])
                                groups = saved_c.get("groups") or []
                                if groups:
                                    rows = [
                                        {
                                            "Group": g.get("label", ""),
                                            "Count": g.get("count", ""),
                                            "Dietary": g.get("dietary_type", ""),
                                        }
                                        for g in groups
                                    ]
                                    st.dataframe(rows, use_container_width=True, hide_index=True)
                                c1, c2 = st.columns(2)
                                if saved_c.get("budget"):
                                    c1.metric(
                                        "Budget",
                                        f"£{saved_c['budget']:,.0f}" if isinstance(saved_c["budget"], (int, float)) else saved_c["budget"],
                                    )
                                if saved_c.get("location"):
                                    c2.metric("Location", saved_c["location"])
                                st.caption("Use the Catering Planner to find matching vendors using this brief →")
                            except Exception:
                                st.warning("Catering data is malformed — please re-enter below.")
                            st.markdown("---")

                        st.markdown("**Update catering requirements:**" if has_catering else "**Attach catering requirements to this event:**")
                        st.caption("Paste your catering requirements as text. To upload a file, use the **Create Event** tab.")
                        update_catering = st.text_area(
                            "Catering requirements text",
                            height=100,
                            key=f"update_catering_{event['id']}",
                            placeholder="e.g. 200 guests: 50 vegan, 100 halal non-veg, 50 vegetarian, budget £3,000, London",
                        )
                        if st.button("✨ Extract & Save Catering", key=f"save_cater_{event['id']}", type="primary"):
                            if not update_catering.strip():
                                st.warning("Please paste some catering requirements text first.")
                            else:
                                with st.spinner("AI is extracting catering requirements…"):
                                    try:
                                        client.save_event_catering_brief(event["id"], update_catering)
                                        st.success("✅ Catering requirements saved!")
                                        st.rerun()
                                    except APIError as exc:
                                        st.error(f"Failed: {exc.detail}")


# ── Shared Events ─────────────────────────────────────────────────────────────
with tab_shared:
    try:
        shared = client.list_shared_events()
    except APIError as exc:
        st.error(f"Could not load shared events: {exc.detail}")
        shared = []

    if not shared:
        st.info("No shared events available.")
    else:
        st.markdown(f"**{len(shared)} shared event(s)**")
        for event in shared:
            dt = datetime.fromisoformat(event["date_time"].replace("Z", "+00:00")) if isinstance(event["date_time"], str) else event["date_time"]
            owner_tag = "👤 You" if event["user_id"] == st.session_state["user_id"] else "👥 Community"
            with st.expander(
                f"🌐 **{event['title']}** — {dt.strftime('%d %b %Y, %H:%M')} | {owner_tag}"
            ):
                if event["description"]:
                    st.markdown(event["description"])
                if event["location"]:
                    st.markdown(f"📍 {event['location']}")
                st.markdown(f"🏷️ {event['category']}")
