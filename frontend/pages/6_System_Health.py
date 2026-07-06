import time
import streamlit as st

from utils.auth import require_auth, show_sidebar, get_client
from client.api_client import APIError, BACKEND_URL

st.set_page_config(page_title="System Health — AI Event Manager", page_icon="🩺", layout="wide")
require_auth()
show_sidebar()

st.title("🩺 System Health Check")
st.markdown("Live validation of every service and API key in your configuration.")
st.caption(f"Backend URL: `{BACKEND_URL}`")

client = get_client()

run = st.button("▶ Run All Checks", type="primary")
if not run:
    st.info("Click **Run All Checks** to validate your configuration.")
    st.stop()


def check_row(label: str, fn, optional: bool = False):
    col_label, col_status, col_detail = st.columns([2, 1, 4])
    col_label.markdown(f"**{label}**" + (" *(optional)*" if optional else ""))
    with col_status:
        with st.spinner(""):
            t0 = time.time()
            try:
                detail = fn()
                elapsed = time.time() - t0
                st.success(f"✅ OK ({elapsed:.2f}s)")
                col_detail.markdown(detail or "")
                return True, detail
            except Exception as exc:
                elapsed = time.time() - t0
                if optional:
                    st.warning(f"⚠️ Skipped ({elapsed:.2f}s)")
                else:
                    st.error(f"❌ Failed ({elapsed:.2f}s)")
                col_detail.markdown(f"`{exc}`")
                return optional, str(exc)


# ── Backend ping ──────────────────────────────────────────────────────────────
st.markdown("### 🌐 Backend API")

def _check_ping():
    data = client.ping()
    return f"Backend at `{BACKEND_URL}` is reachable — status: `{data['status']}`"

results = {}
results["Backend"] = check_row("Backend API ping", _check_ping)

st.markdown("---")

# ── Full health from backend ──────────────────────────────────────────────────
st.markdown("### 🔍 Service Health (from backend)")

try:
    health_data = client.health()
    checks = health_data.get("checks", {})

    st.markdown("**PostgreSQL**")
    pg = checks.get("postgres", {})
    if pg.get("ok"):
        st.success(f"✅ OK ({pg.get('ms')}ms) — {pg.get('detail')}")
    else:
        st.error(f"❌ Failed — {pg.get('detail')}")
    results["PostgreSQL"] = (pg.get("ok", False), pg.get("detail", ""))

    st.markdown("**ChromaDB**")
    chroma = checks.get("chromadb", {})
    if chroma.get("ok"):
        st.success(f"✅ OK ({chroma.get('ms')}ms) — {chroma.get('detail')}")
    else:
        st.error(f"❌ Failed — {chroma.get('detail')}")
    results["ChromaDB"] = (chroma.get("ok", False), chroma.get("detail", ""))

    st.markdown("**Azure OpenAI**")
    oai = checks.get("azure_openai", {})
    if oai.get("ok"):
        st.success(f"✅ OK ({oai.get('ms')}ms) — {oai.get('detail')}")
    else:
        st.error(f"❌ Failed — {oai.get('detail')}")
    results["Azure OpenAI"] = (oai.get("ok", False), oai.get("detail", ""))

    st.markdown("**Geoapify**")
    geo = checks.get("geoapify", {})
    if geo.get("ok"):
        st.success(f"✅ OK ({geo.get('ms')}ms) — {geo.get('detail')}")
    else:
        st.warning(f"⚠️ — {geo.get('detail')}")
    results["Geoapify"] = (geo.get("ok", False), geo.get("detail", ""))

except APIError as exc:
    st.error(f"Could not reach backend health endpoint: {exc.detail}")
except Exception as exc:
    st.error(f"Unexpected error: {exc}")

st.markdown("---")

# ── Summary ───────────────────────────────────────────────────────────────────
st.markdown("### Summary")
OPTIONAL_CHECKS = {"Geoapify"}
critical = {k: v for k, v in results.items() if k not in OPTIONAL_CHECKS}
passed = sum(1 for ok, _ in critical.values() if ok)

if passed == len(critical):
    st.success(f"✅ All {len(critical)} critical checks passed.")
else:
    failed = [k for k, (ok, _) in critical.items() if not ok]
    st.error(f"{passed}/{len(critical)} critical checks passed. Fix: **{', '.join(failed)}**")
