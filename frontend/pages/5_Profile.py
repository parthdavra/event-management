import streamlit as st

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="Profile — AI Event Manager", page_icon="⚙️", layout="centered")
require_auth()
show_sidebar()

st.title("⚙️ Profile")

client = get_client()

try:
    profile = client.me()
except APIError as exc:
    st.error(f"Could not load profile: {exc.detail}")
    st.stop()

# ── Profile info ──────────────────────────────────────────────────────────────
st.subheader("Account Details")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Username:** {profile['username']}")
    st.markdown(f"**Email:** {profile['email']}")
with col2:
    created = profile.get("created_at", "")
    if isinstance(created, str) and created:
        try:
            from datetime import datetime
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            created = created_dt.strftime("%d %b %Y")
        except Exception:
            pass
    st.markdown(f"**Member since:** {created}")

st.markdown("---")

# ── Change password ───────────────────────────────────────────────────────────
st.subheader("Change Password")
with st.form("change_password_form"):
    old_pass = st.text_input("Current Password", type="password")
    new_pass = st.text_input("New Password", type="password")
    confirm_new = st.text_input("Confirm New Password", type="password")
    submitted = st.form_submit_button("Update Password", type="primary")

if submitted:
    if old_pass and new_pass and confirm_new:
        if new_pass != confirm_new:
            st.error("New passwords do not match.")
        elif len(new_pass) < 6:
            st.error("Password must be at least 6 characters.")
        else:
            try:
                client.change_password(old_pass, new_pass)
                st.success("Password updated successfully!")
            except APIError as exc:
                st.error(exc.detail)
    else:
        st.warning("Please fill in all fields.")
