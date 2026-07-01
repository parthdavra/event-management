import streamlit as st
from datetime import datetime, date, time
from modules.auth import require_auth, show_sidebar
from modules.events import (
    create_event, get_user_events, get_shared_events,
    update_event, delete_event,
)

st.set_page_config(page_title="Events — AI Event Manager", page_icon="📅", layout="wide")
require_auth()
show_sidebar()

st.title("📅 Events")

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
            event, error = create_event(
                user_id=st.session_state["user_id"],
                title=title,
                description=description,
                date_time=datetime.combine(event_date, event_time),
                location=location,
                category=category,
                is_shared=is_shared,
            )
            if event:
                st.success(f"✅ Event '{title}' created!")
                st.rerun()
            else:
                st.error(f"Error: {error}")
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

    events = get_user_events(
        st.session_state["user_id"],
        category_filter=cat_filter if cat_filter != "All" else None,
        sort_by=sort_by,
    )

    if not events:
        st.info("No events yet. Create your first event in the 'Create Event' tab!")
    else:
        st.markdown(f"**{len(events)} event(s)**")

        if "edit_event_id" not in st.session_state:
            st.session_state["edit_event_id"] = None

        for event in events:
            header = (
                f"{'🌐' if event['is_shared'] else '🔒'} **{event['title']}** "
                f"— {event['date_time'].strftime('%d %b %Y, %H:%M')} | {event['category']}"
            )
            with st.expander(header):
                if event["id"] == st.session_state["edit_event_id"]:
                    # ── Edit form ────────────────────────────────────────────
                    with st.form(f"edit_form_{event['id']}"):
                        new_title = st.text_input("Title", value=event["title"])
                        col1, col2 = st.columns(2)
                        with col1:
                            new_date = st.date_input("Date", value=event["date_time"].date())
                            new_cat = st.selectbox(
                                "Category", CATEGORIES,
                                index=CATEGORIES.index(event["category"]) if event["category"] in CATEGORIES else 0,
                            )
                        with col2:
                            new_time = st.time_input("Time", value=event["date_time"].time())
                            new_shared = st.checkbox("Shared", value=event["is_shared"])
                        new_loc = st.text_input("Location", value=event["location"] or "")
                        new_desc = st.text_area("Description", value=event["description"] or "")
                        col_save, col_cancel = st.columns(2)
                        with col_save:
                            save = st.form_submit_button("Save", type="primary", use_container_width=True)
                        with col_cancel:
                            cancel = st.form_submit_button("Cancel", use_container_width=True)

                    if save:
                        ok, err = update_event(
                            event["id"], st.session_state["user_id"],
                            title=new_title,
                            date_time=datetime.combine(new_date, new_time),
                            category=new_cat,
                            location=new_loc,
                            description=new_desc,
                            is_shared=new_shared,
                        )
                        if ok:
                            st.session_state["edit_event_id"] = None
                            st.rerun()
                        else:
                            st.error(err)
                    if cancel:
                        st.session_state["edit_event_id"] = None
                        st.rerun()

                else:
                    # ── Read mode ────────────────────────────────────────────
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
                                ok, err = delete_event(event["id"], st.session_state["user_id"])
                                if ok:
                                    st.session_state.pop(f"confirm_del_{event['id']}", None)
                                    st.rerun()
                                else:
                                    st.error(err)
                        with c2:
                            if st.button("Cancel", key=f"no_{event['id']}"):
                                st.session_state.pop(f"confirm_del_{event['id']}", None)
                                st.rerun()

# ── Shared Events ─────────────────────────────────────────────────────────────
with tab_shared:
    shared = get_shared_events()
    if not shared:
        st.info("No shared events available.")
    else:
        st.markdown(f"**{len(shared)} shared event(s)**")
        for event in shared:
            owner_tag = "👤 You" if event["user_id"] == st.session_state["user_id"] else "👥 Community"
            with st.expander(
                f"🌐 **{event['title']}** — {event['date_time'].strftime('%d %b %Y, %H:%M')} | {owner_tag}"
            ):
                if event["description"]:
                    st.markdown(event["description"])
                if event["location"]:
                    st.markdown(f"📍 {event['location']}")
                st.markdown(f"🏷️ {event['category']}")
