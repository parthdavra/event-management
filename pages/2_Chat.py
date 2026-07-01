import streamlit as st
from modules.auth import require_auth, show_sidebar
from modules.events import get_user_events, get_shared_events
from modules.chat import get_messages, send_message

st.set_page_config(page_title="Chat — AI Event Manager", page_icon="💬", layout="wide")
require_auth()
show_sidebar()

st.title("💬 Chat")

# ── Build channel list ────────────────────────────────────────────────────────
user_events = get_user_events(st.session_state["user_id"])
shared_events = get_shared_events()

channel_options = ["🌐 Global Chat"]
event_map: dict[str, int] = {}

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

event_id = event_map.get(selected)  # None = global chat

st.markdown(f"**Channel:** {selected}")
st.markdown("---")

# ── Messages ──────────────────────────────────────────────────────────────────
messages = get_messages(event_id=event_id)

if not messages:
    st.info("No messages yet. Start the conversation!")
else:
    for msg in messages:
        is_own = msg["username"] == st.session_state.get("username")
        role = "user" if is_own else "assistant"
        with st.chat_message(role):
            st.markdown(f"**{msg['username']}** · *{msg['created_at'].strftime('%H:%M, %d %b')}*")
            st.markdown(msg["message"])

# ── Input ─────────────────────────────────────────────────────────────────────
prompt = st.chat_input(f"Message #{selected.split(' ', 1)[-1]}…")
if prompt:
    ok, err = send_message(
        user_id=st.session_state["user_id"],
        message=prompt,
        event_id=event_id,
    )
    if ok:
        st.rerun()
    else:
        st.error(f"Failed to send: {err}")
