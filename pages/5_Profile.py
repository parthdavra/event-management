import streamlit as st
from modules.auth import require_auth, show_sidebar, get_user_profile, change_password

st.set_page_config(page_title="Profile — AI Event Manager", page_icon="⚙️", layout="centered")
require_auth()
show_sidebar()

st.title("⚙️ Profile")

profile = get_user_profile(st.session_state["user_id"])
if not profile:
    st.error("Could not load profile.")
    st.stop()

# ── Profile info ──────────────────────────────────────────────────────────────
st.subheader("Account Details")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Username:** {profile['username']}")
    st.markdown(f"**Email:** {profile['email']}")
with col2:
    st.markdown(f"**Member since:** {profile['created_at'].strftime('%d %b %Y')}")

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
            ok, err = change_password(
                st.session_state["user_id"], old_pass, new_pass
            )
            if ok:
                st.success("Password updated successfully!")
            else:
                st.error(err)
    else:
        st.warning("Please fill in all fields.")
