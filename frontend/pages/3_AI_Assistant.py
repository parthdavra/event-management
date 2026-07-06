import streamlit as st

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError

st.set_page_config(page_title="AI Assistant — AI Event Manager", page_icon="🤖", layout="wide")
require_auth()
show_sidebar()

st.title("🤖 AI Assistant")

client = get_client()

# ── Session state ─────────────────────────────────────────────────────────────
if "ai_messages" not in st.session_state:
    st.session_state["ai_messages"] = []

# ── Sidebar settings ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown("### ⚙️ AI Settings")
    use_rag = st.toggle("Use Indexed Documents (RAG)", value=True)

    try:
        sources = client.list_sources()
        indexed = [s for s in sources if s["status"] == "indexed"]
    except APIError:
        indexed = []

    if use_rag:
        if indexed:
            st.markdown(f"**{len(indexed)} source(s) loaded:**")
            for src in indexed:
                st.markdown(f"- {src['source_name']} *({src['chunk_count']} chunks)*")
        else:
            st.info("No indexed documents. Upload files on the Data Indexing page.")

    st.markdown("---")

    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state["ai_messages"] = []
        st.rerun()

# ── Introductory message ──────────────────────────────────────────────────────
if not st.session_state["ai_messages"]:
    with st.chat_message("assistant"):
        if use_rag and indexed:
            st.markdown(
                f"Hi! I'm your AI assistant with access to **{len(indexed)} indexed document(s)**. "
                "Ask me anything about your uploaded files or event planning in general."
            )
        else:
            st.markdown(
                "Hi! I'm your AI event planning assistant. "
                "Ask me about venues, scheduling, catering, or anything event-related."
            )

# ── Chat history ──────────────────────────────────────────────────────────────
for msg in st.session_state["ai_messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── User input ────────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask a question…")
if prompt:
    st.session_state["ai_messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state["ai_messages"][:-1]
    ]

    with st.chat_message("assistant"):
        response = None
        try:
            with st.status("🔄 Processing your question…", expanded=True) as status:

                # ── Step 1: Input Guardrail ───────────────────────────────────
                st.write("**🛡️ Step 1 — Input Guardrail**")
                st.caption("  Checking for abuse, spelling errors, and sarcasm…")

                guard_in = client.ai_check_input(prompt)

                if not guard_in["allowed"]:
                    status.update(label="🚫 Blocked by guardrail", state="error", expanded=True)
                    abuse_msg = (
                        "I'm here to help with event planning — venues, catering, logistics, budgets. "
                        "Please keep the conversation respectful."
                    )
                    st.error(f"Blocked: {guard_in['rejection_reason']}")
                    st.session_state["ai_messages"].append({"role": "assistant", "content": abuse_msg})
                    st.stop()

                st.write(f"  ✅ Clean · Category: `{guard_in['category']}`")

                if guard_in.get("was_corrected"):
                    for c in guard_in.get("corrections", []):
                        st.write(f"  ✏️ Spell-corrected: `{c['original']}` → `{c['corrected']}`")
                if guard_in.get("is_sarcastic"):
                    st.write(f"  😏 Sarcasm detected — real intent: *{guard_in['real_intent']}*")

                effective_query = guard_in["real_intent"]

                # ── Step 2: RAG search or direct chat ────────────────────────
                if use_rag and indexed:
                    st.write(f"**🔍 Step 2 — RAG Search**")
                    collections = client.list_collections()
                    st.caption(f"  Querying **{len(collections)}** ChromaDB collection(s)…")

                    rag_result = client.ai_rag(
                        query=effective_query,
                        collection_names=collections if collections else None,
                        n_results=5,
                        chat_history=history,
                    )

                    st.write(f"**🤖 Step 3 — LLM Generation**")
                    st.caption(f"  Confidence: `{rag_result['confidence']}`")
                    response = rag_result["answer"]

                    if rag_result.get("sources_used"):
                        st.write(f"  Sources: " + ", ".join(f"`{s}`" for s in rag_result["sources_used"][:3]))

                else:
                    st.write(f"**🤖 Step 2 — Direct LLM Chat**")
                    st.caption("  RAG disabled — answering from general knowledge…")
                    response = client.ai_chat(effective_query, history)

                status.update(label="✅ Answer ready", state="complete", expanded=False)

            st.markdown(response)
            st.session_state["ai_messages"].append({"role": "assistant", "content": response})

        except APIError as exc:
            err = f"API Error ({exc.status_code}): {exc.detail}"
            st.error(err)
            st.session_state["ai_messages"].append({"role": "assistant", "content": err})
        except Exception as exc:
            import traceback
            err = f"Error: {exc}"
            st.error(err)
            st.caption(traceback.format_exc())
            st.session_state["ai_messages"].append({"role": "assistant", "content": err})
