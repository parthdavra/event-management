import streamlit as st
from modules.database import init_db
from modules.auth import register_user, login_user, is_authenticated

init_db()

st.set_page_config(
    page_title="AI Event Manager",
    page_icon="📅",
    layout="centered",
)

# Redirect authenticated users straight to Events
if is_authenticated():
    st.switch_page("pages/1_Events.py")

st.title("📅 AI Event Management Platform")
st.markdown(
    "Plan, organise, and manage events with AI-powered assistance — "
    "real-time chat, RAG document intelligence, and smart scheduling."
)
st.markdown("---")

tab_login, tab_signup = st.tabs(["Login", "Sign Up"])

# ── Login ────────────────────────────────────────────────────────────────────
with tab_login:
    with st.form("login_form"):
        username = st.text_input("Username", placeholder="Enter your username")
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        login_submitted = st.form_submit_button("Login", use_container_width=True, type="primary")

    if login_submitted:
        if username and password:
            user, error = login_user(username, password)
            if user:
                st.session_state["user_id"] = user["id"]
                st.session_state["username"] = user["username"]
                st.rerun()
            else:
                st.error(error)
        else:
            st.warning("Please enter your username and password.")

# ── Sign Up ───────────────────────────────────────────────────────────────────
with tab_signup:
    with st.form("signup_form"):
        new_username = st.text_input("Username", placeholder="Choose a username", key="su_user")
        new_email = st.text_input("Email", placeholder="your@email.com", key="su_email")
        new_password = st.text_input(
            "Password", type="password", placeholder="At least 6 characters", key="su_pass"
        )
        confirm_password = st.text_input(
            "Confirm Password", type="password", placeholder="Repeat password", key="su_conf"
        )
        signup_submitted = st.form_submit_button(
            "Create Account", use_container_width=True, type="primary"
        )

    if signup_submitted:
        if new_username and new_email and new_password and confirm_password:
            if new_password != confirm_password:
                st.error("Passwords do not match.")
            elif len(new_password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                user, error = register_user(new_username, new_email, new_password)
                if user:
                    st.session_state["user_id"] = user["id"]
                    st.session_state["username"] = user["username"]
                    st.success("Account created! Redirecting…")
                    st.rerun()
                else:
                    st.error(error)
        else:
            st.warning("Please fill in all fields.")
