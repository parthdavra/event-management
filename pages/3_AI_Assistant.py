import streamlit as st
from modules.auth import require_auth, show_sidebar
from modules.rag import (
    is_configured,
    generate_rag_response,
    generate_chat_response,
    query_collection,
    query_multiple_collections,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
)
from modules.indexing import get_user_sources, get_all_user_collections
from modules.guardrails import check_input, ABUSE_MESSAGE

st.set_page_config(page_title="AI Assistant — AI Event Manager", page_icon="🤖", layout="wide")
require_auth()
show_sidebar()

st.title("🤖 AI Assistant")

# ── Config check ──────────────────────────────────────────────────────────────
if not is_configured():
    st.warning(
        "Azure OpenAI is not configured. Set `AZURE_OPENAI_API_KEY` and "
        "`AZURE_OPENAI_ENDPOINT` environment variables (or in a `.env` file) to enable the AI assistant."
    )
    st.code(
        "AZURE_OPENAI_API_KEY=your-key\n"
        "AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/",
        language="bash",
    )
    st.stop()

# ── Session state ─────────────────────────────────────────────────────────────
if "ai_messages" not in st.session_state:
    st.session_state["ai_messages"] = []

# ── Sidebar settings ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown("### ⚙️ AI Settings")
    use_rag = st.toggle("Use Indexed Documents (RAG)", value=True)

    sources = get_user_sources(st.session_state["user_id"])
    indexed = [s for s in sources if s["status"] == "indexed"]

    if use_rag:
        if indexed:
            st.markdown(f"**{len(indexed)} source(s) loaded:**")
            for src in indexed:
                st.markdown(f"- {src['source_name']} *({src['chunk_count']} chunks)*")
        else:
            st.info("No indexed documents. Upload files on the Data Indexing page.")

    st.markdown("---")
    # Model info
    model_short = AZURE_OPENAI_DEPLOYMENT or "not set"
    endpoint_host = (AZURE_OPENAI_ENDPOINT or "").replace("https://", "").split(".")[0] or "not set"
    st.caption(f"**Model:** `{model_short}`")
    st.caption(f"**Endpoint:** `{endpoint_host}…`")
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
                st.caption("&nbsp;&nbsp;Checking for abuse, spelling errors, and sarcasm…")

                guard_in = check_input(prompt)

                if not guard_in["allowed"]:
                    status.update(label="🚫 Blocked by guardrail", state="error", expanded=True)
                    st.error(f"Blocked: {guard_in['rejection_reason']}")
                    st.session_state["ai_messages"].append(
                        {"role": "assistant", "content": ABUSE_MESSAGE}
                    )
                    st.stop()

                st.write(f"&nbsp;&nbsp;✅ Clean · Category: `{guard_in['category']}`")

                if guard_in.get("was_corrected"):
                    for c in guard_in.get("corrections", []):
                        st.write(f"&nbsp;&nbsp;✏️ Spell-corrected: `{c['original']}` → `{c['corrected']}`")

                if guard_in.get("is_sarcastic"):
                    st.write(f"&nbsp;&nbsp;😏 Sarcasm detected — real intent: *{guard_in['real_intent']}*")

                effective_query = guard_in["real_intent"]

                # ── Step 2: RAG search ────────────────────────────────────────
                if use_rag and indexed:
                    collections = get_all_user_collections(st.session_state["user_id"])

                    st.write(f"**🔍 Step 2 — RAG Search**")
                    st.caption(
                        f"&nbsp;&nbsp;Querying **{len(collections)}** ChromaDB collection(s) "
                        f"across **{len(indexed)}** indexed source(s)…"
                    )

                    # Query each collection separately so we can show per-source results
                    per_source_docs: dict = {}
                    all_docs = []
                    for col_name in collections:
                        try:
                            docs, _ = query_collection(col_name, effective_query, n_results=3)
                            if docs:
                                per_source_docs[col_name] = docs
                                all_docs.extend(docs)
                        except Exception:
                            continue
                    all_docs = all_docs[:10]  # cap at 10 chunks total

                    if all_docs:
                        st.write(
                            f"&nbsp;&nbsp;✅ Retrieved **{len(all_docs)}** relevant chunk(s) "
                            f"from **{len(per_source_docs)}** collection(s)"
                        )
                        # Show which sources contributed
                        for src in indexed:
                            col = src.get("collection_name", "")
                            if col in per_source_docs:
                                n = len(per_source_docs[col])
                                st.write(
                                    f"&nbsp;&nbsp;&nbsp;&nbsp;📄 `{src['source_name']}` "
                                    f"→ {n} chunk(s) matched"
                                )

                        # Preview of top retrieved chunk
                        with st.expander("📄 Top retrieved chunk (preview)", expanded=False):
                            st.caption("Most relevant passage found in your documents:")
                            st.text(all_docs[0][:400] + ("…" if len(all_docs[0]) > 400 else ""))
                    else:
                        st.write(
                            "  ⚠️ No relevant chunks found in indexed documents — "
                            "answering from general knowledge"
                        )

                    # ── Step 3: LLM with RAG context ─────────────────────────
                    ctx_words = sum(len(d.split()) for d in all_docs)
                    st.write(f"**🤖 Step 3 — LLM Generation**")
                    st.caption(
                        f"&nbsp;&nbsp;Sending query + **{len(all_docs)} context chunks** "
                        f"(~{ctx_words:,} words) to `{AZURE_OPENAI_DEPLOYMENT}`…"
                    )

                    response = generate_rag_response(effective_query, all_docs, history)

                    st.write(
                        f"&nbsp;&nbsp;✅ Response generated · "
                        f"**{len(response.split()):,} words**"
                    )

                else:
                    # ── Direct chat (no RAG) ──────────────────────────────────
                    st.write(f"**🤖 Step 2 — Direct LLM Chat**")
                    st.caption(
                        f"&nbsp;&nbsp;RAG disabled — sending query directly to "
                        f"`{AZURE_OPENAI_DEPLOYMENT}` (no document context)…"
                    )

                    response = generate_chat_response(effective_query, history)

                    st.write(
                        f"&nbsp;&nbsp;✅ Response generated · "
                        f"**{len(response.split()):,} words**"
                    )

                status.update(label="✅ Answer ready", state="complete", expanded=False)

            # ── Render answer ─────────────────────────────────────────────────
            st.markdown(response)
            st.session_state["ai_messages"].append({"role": "assistant", "content": response})

        except Exception as e:
            import traceback
            err = f"⚠️ Error: {e}"
            st.error(err)
            st.caption(traceback.format_exc())
            st.session_state["ai_messages"].append({"role": "assistant", "content": err})
