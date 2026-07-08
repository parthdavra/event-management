"""
Streamlit session authentication helpers.
All frontend pages import from here — never from the API client directly.
"""

import streamlit as st

from client.api_client import EventManagementClient, APIError


def get_client() -> EventManagementClient:
    """Return an authenticated API client using the token in session state."""
    return EventManagementClient(token=st.session_state.get("token"))


def set_session(token: str, user: dict) -> None:
    """Populate session state and mirror the token into the URL so a page
    refresh (which wipes st.session_state) can restore the session instead
    of bouncing the user back to the login page."""
    st.session_state["token"] = token
    st.session_state["user_id"] = user["id"]
    st.session_state["username"] = user["username"]
    st.session_state["email"] = user["email"]
    st.query_params["token"] = token


def _restore_session_from_query_params() -> None:
    if st.session_state.get("token"):
        return
    qp_token = st.query_params.get("token")
    if not qp_token:
        return
    try:
        me = EventManagementClient(token=qp_token).me()
        st.session_state["token"] = qp_token
        st.session_state["user_id"] = me["id"]
        st.session_state["username"] = me["username"]
        st.session_state["email"] = me["email"]
    except APIError:
        # Token was rejected (expired/invalid) — drop it so we stop retrying.
        if "token" in st.query_params:
            del st.query_params["token"]
    except Exception:
        # Backend unreachable — leave the query param alone and try again
        # on the next rerun rather than logging the user out.
        pass


def is_authenticated() -> bool:
    _restore_session_from_query_params()
    return bool(st.session_state.get("token") and st.session_state.get("user_id"))


def logout() -> None:
    for key in ("token", "user_id", "username", "email"):
        st.session_state.pop(key, None)
    if "token" in st.query_params:
        del st.query_params["token"]


def require_auth() -> None:
    """Stop the page and show a login prompt if the user is not authenticated."""
    if not is_authenticated():
        st.warning("Please log in to access this page.")
        if st.button("Go to Login", type="primary"):
            st.switch_page("app.py")
        st.stop()


def show_sidebar() -> None:
    with st.sidebar:
        st.markdown(f"### {st.session_state.get('username', '')}")
        st.markdown("---")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Events.py", label="Events", icon="📅")
        st.page_link("pages/2_Chat.py", label="Chat", icon="💬")
        st.page_link("pages/3_AI_Assistant.py", label="AI Assistant", icon="🤖")
        st.page_link("pages/4_Data_Indexing.py", label="Data Indexing", icon="🗂️")
        st.page_link("pages/5_Profile.py", label="Profile", icon="⚙️")
        st.page_link("pages/6_System_Health.py", label="System Health", icon="🩺")
        st.page_link("pages/7_Smart_Planner.py", label="Smart Planner", icon="🎯")
        st.page_link("pages/8_Query_Insights.py", label="Query Insights", icon="📊")
        st.page_link("pages/9_Business_Metrics.py", label="Business Metrics", icon="📈")
        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            logout()
            st.switch_page("app.py")
