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
        description = st.text_area("Description", placeholder="Event details…", height=100)
        create_submitted = st.form_submit_button("Create Event", type="primary", use_container_width=True)

    if create_submitted:
        if title:
            try:
                dt_str = datetime.combine(event_date, event_time).isoformat()
                client.create_event(
                    title=title,
                    date_time=dt_str,
                    description=description or None,
                    location=location or None,
                    category=category,
                    is_shared=is_shared,
                )
                st.success(f"Event '{title}' created!")
                st.rerun()
            except APIError as exc:
                st.error(f"Error: {exc.detail}")
        else:
            st.warning("Event title is required.")

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
        st.info("No events yet. Create your first event in the 'Create Event' tab!")
    else:
        st.markdown(f"**{len(events)} event(s)**")

        if "edit_event_id" not in st.session_state:
            st.session_state["edit_event_id"] = None

        for event in events:
            dt = datetime.fromisoformat(event["date_time"].replace("Z", "+00:00")) if isinstance(event["date_time"], str) else event["date_time"]
            header = (
                f"{'🌐' if event['is_shared'] else '🔒'} **{event['title']}** "
                f"— {dt.strftime('%d %b %Y, %H:%M')} | {event['category']}"
            )
            with st.expander(header):
                if event["id"] == st.session_state["edit_event_id"]:
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
