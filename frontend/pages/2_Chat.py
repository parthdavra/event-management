import streamlit as st
from datetime import datetime

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="Chat — AI Event Manager", page_icon="💬", layout="wide")
require_auth()
show_sidebar()

st.title("💬 Chat")

client = get_client()

# ── Build channel list ────────────────────────────────────────────────────────
try:
    user_events = client.list_events()
except APIError:
    user_events = []

try:
    shared_events = client.list_shared_events()
except APIError:
    shared_events = []

channel_options = ["🌐 Global Chat"]
event_map: dict = {}

user_event_ids = {e["id"] for e in user_events}

for e in user_events:
    label = f"📅 {e['title']}"
    channel_options.append(label)
    event_map[label] = e["id"]

for e in shared_events:
    if e["id"] not in user_event_ids:
        label = f"🌐 {e['title']} (shared)"
        channel_options.append(label)
        event_map[label] = e["id"]

# ── Channel selector ──────────────────────────────────────────────────────────
col_sel, col_refresh = st.columns([5, 1])
with col_sel:
    selected = st.selectbox("Channel", channel_options, label_visibility="collapsed")
with col_refresh:
    if st.button("🔄 Refresh"):
        st.rerun()

event_id = event_map.get(selected)
st.markdown(f"**Channel:** {selected}")
st.markdown("---")

# ── Messages ──────────────────────────────────────────────────────────────────
try:
    messages = client.get_messages(event_id=event_id)
except APIError as exc:
    st.error(f"Could not load messages: {exc.detail}")
    messages = []

if not messages:
    st.info("No messages yet. Start the conversation!")
else:
    for msg in messages:
        is_own = msg["username"] == st.session_state.get("username")
        role = "user" if is_own else "assistant"
        created_at = msg["created_at"]
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                ts = created_at.strftime("%H:%M, %d %b")
            except Exception:
                ts = created_at
        else:
            ts = created_at.strftime("%H:%M, %d %b") if hasattr(created_at, "strftime") else str(created_at)
        with st.chat_message(role):
            st.markdown(f"**{msg['username']}** · *{ts}*")
            st.markdown(msg["message"])

# ── Input ─────────────────────────────────────────────────────────────────────
prompt = st.chat_input(f"Message #{selected.split(' ', 1)[-1]}…")
if prompt:
    try:
        client.send_message(message=prompt, event_id=event_id)
        st.rerun()
    except APIError as exc:
        st.error(f"Failed to send: {exc.detail}")
