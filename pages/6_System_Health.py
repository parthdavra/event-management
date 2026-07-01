import time
import os
import streamlit as st
from modules.auth import require_auth, show_sidebar

st.set_page_config(page_title="System Health — AI Event Manager", page_icon="🩺", layout="wide")
require_auth()
show_sidebar()

st.title("🩺 System Health Check")
st.markdown("Live validation of every service and API key in your configuration.")

# ── Config snapshot ────────────────────────────────────────────────────────────
with st.expander("🔍 Current Configuration (click to expand)"):
    from modules.rag import (
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT,
        AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        AZURE_OPENAI_API_VERSION, CHROMA_PERSIST_DIR,
    )
    from modules.api_fetcher import FOURSQUARE_API_KEY, GEOAPIFY_API_KEY
    from modules.database import DATABASE_URL

    def mask(val: str, show: int = 6) -> str:
        return val[:show] + "…" + val[-4:] if len(val) > show + 4 else "***"

    rows = {
        "DATABASE_URL": mask(DATABASE_URL) if DATABASE_URL else "❌ not set",
        "AZURE_OPENAI_ENDPOINT": AZURE_OPENAI_ENDPOINT or "❌ not set",
        "AZURE_OPENAI_DEPLOYMENT": AZURE_OPENAI_DEPLOYMENT or "❌ not set",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": AZURE_OPENAI_EMBEDDING_DEPLOYMENT or "❌ not set",
        "AZURE_OPENAI_API_VERSION": AZURE_OPENAI_API_VERSION or "❌ not set",
        "AZURE_OPENAI_API_KEY": mask(AZURE_OPENAI_API_KEY) if AZURE_OPENAI_API_KEY else "❌ not set",
        "CHROMA_PERSIST_DIR": CHROMA_PERSIST_DIR or "❌ not set",
        "GEOAPIFY_API_KEY": mask(GEOAPIFY_API_KEY) if GEOAPIFY_API_KEY else "❌ not set",
        "FOURSQUARE_API_KEY": mask(FOURSQUARE_API_KEY) if FOURSQUARE_API_KEY else "❌ not set",
    }
    for k, v in rows.items():
        st.markdown(f"- **{k}**: `{v}`")

# ── Helper ────────────────────────────────────────────────────────────────────

def check(label: str, fn, optional: bool = False):
    """
    Run fn(), render a status row.
    fn() should return a detail string on success, or raise.
    If optional=True a failure shows ⚠️ and is not counted against the total.
    Returns (ok, detail).
    """
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
            except Exception as e:
                elapsed = time.time() - t0
                if optional:
                    st.warning(f"⚠️ Skipped ({elapsed:.2f}s)")
                else:
                    st.error(f"❌ Failed ({elapsed:.2f}s)")
                col_detail.markdown(f"`{e}`")
                return optional, str(e)   # optional failures are treated as "passing"


# ── Run checks ────────────────────────────────────────────────────────────────

run = st.button("▶ Run All Checks", type="primary")
if not run:
    st.info("Click **Run All Checks** to validate your configuration.")
    st.stop()

results = {}

# 1. PostgreSQL
st.markdown("### 🗄️ Database")
def _check_db():
    from modules.database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version()")).fetchone()
    return f"PostgreSQL: `{row[0].split(',')[0]}`"
results["PostgreSQL"] = check("PostgreSQL connection", _check_db)

st.markdown("---")

# 2. Azure OpenAI — chat
st.markdown("### 🤖 Azure OpenAI")
def _check_chat():
    import socket
    from modules.rag import _openai_client, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
    if not AZURE_OPENAI_API_KEY:
        raise ValueError("AZURE_OPENAI_API_KEY is not set in .env")
    if not AZURE_OPENAI_ENDPOINT:
        raise ValueError("AZURE_OPENAI_ENDPOINT is not set in .env")
    # DNS check first — gives a clearer error than a generic ConnectionError
    host = AZURE_OPENAI_ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
    try:
        socket.gethostbyname(host)
    except socket.gaierror:
        raise ValueError(
            f"DNS lookup failed for `{host}` — "
            "check AZURE_OPENAI_ENDPOINT in your .env; the resource may not exist or the name is wrong"
        )
    client = _openai_client()
    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
        max_tokens=5,
        temperature=0,
    )
    reply = resp.choices[0].message.content.strip()
    return f"Deployment `{AZURE_OPENAI_DEPLOYMENT}` → replied: `{reply}`"
results["Chat"] = check("Chat deployment (GPT)", _check_chat)

def _check_embeddings():
    import socket
    from modules.rag import _openai_client, AZURE_OPENAI_EMBEDDING_DEPLOYMENT, AZURE_OPENAI_ENDPOINT
    host = AZURE_OPENAI_ENDPOINT.replace("https://", "").replace("http://", "").rstrip("/")
    try:
        socket.gethostbyname(host)
    except socket.gaierror:
        raise ValueError(f"DNS lookup failed for `{host}` — fix AZURE_OPENAI_ENDPOINT first")
    client = _openai_client()
    resp = client.embeddings.create(model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT, input=["test"])
    dims = len(resp.data[0].embedding)
    return f"Deployment `{AZURE_OPENAI_EMBEDDING_DEPLOYMENT}` → vector dims: `{dims}`"
results["Embeddings"] = check("Embeddings deployment", _check_embeddings)

st.markdown("---")

# 3. ChromaDB
st.markdown("### 🗃️ ChromaDB (Vector Store)")
def _check_chroma():
    import chromadb
    from modules.rag import CHROMA_PERSIST_DIR
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    cols = client.list_collections()
    return f"Path `{CHROMA_PERSIST_DIR}` — `{len(cols)}` collection(s) stored"
results["ChromaDB"] = check("ChromaDB persistence", _check_chroma)

st.markdown("---")

# 4. Geocoding
st.markdown("### 🌍 Geocoding & Venue APIs")

def _check_geoapify_geocoding():
    import requests
    from modules.api_fetcher import GEOAPIFY_API_KEY
    if not GEOAPIFY_API_KEY:
        raise ValueError("GEOAPIFY_API_KEY not set")
    r = requests.get(
        "https://api.geoapify.com/v1/geocode/search",
        params={"text": "London", "type": "city", "limit": 1, "apiKey": GEOAPIFY_API_KEY},
        timeout=10,
    )
    r.raise_for_status()
    features = r.json().get("features", [])
    if not features:
        raise ValueError("No results returned from Geoapify geocoding")
    p = features[0]["properties"]
    return f"Resolved `London` → ({p['lat']:.4f}, {p['lon']:.4f}) via Geoapify (primary geocoder)"
results["Geocoding"] = check("Geoapify Geocoding (primary)", _check_geoapify_geocoding)

def _check_photon():
    import requests
    r = requests.get(
        "https://photon.komoot.io/api/",
        params={"q": "London", "limit": 1, "layer": "city"},
        timeout=8,
    )
    if r.status_code == 403:
        raise ValueError(
            "Photon blocked this IP (common from Docker/cloud containers). "
            "This is expected — Geoapify is used as the primary geocoder."
        )
    r.raise_for_status()
    features = r.json().get("features", [])
    if not features:
        raise ValueError("No results returned")
    name = features[0]["properties"].get("name", "?")
    lon, lat = features[0]["geometry"]["coordinates"]
    return f"Resolved `London` → `{name}` at ({lat:.4f}, {lon:.4f})"
results["Photon"] = check("Photon geocoding (fallback)", _check_photon, optional=True)

# 5. Overpass / OSM
def _check_overpass():
    import requests
    from modules.api_fetcher import OVERPASS_MIRRORS
    lat, lon = 51.5074, -0.1278
    bbox = f"around:500,{lat},{lon}"
    query = f'[out:json][timeout:15];(node["amenity"="cafe"]({bbox}););out center;'
    last_err = None
    for mirror in OVERPASS_MIRRORS:
        try:
            r = requests.post(mirror, data={"data": query}, timeout=20)
            if r.status_code == 200:
                count = len(r.json().get("elements", []))
                return f"Mirror `{mirror.split('/')[2]}` → `{count}` elements returned"
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
    raise ValueError(f"All mirrors failed — last error: {last_err}")
results["Overpass"] = check("Overpass / OpenStreetMap", _check_overpass)

# 6. Geoapify
def _check_geoapify():
    import requests
    from modules.api_fetcher import GEOAPIFY_API_KEY
    if not GEOAPIFY_API_KEY:
        raise ValueError("GEOAPIFY_API_KEY not set")
    r = requests.get(
        "https://api.geoapify.com/v2/places",
        params={
            "categories": "catering.restaurant",
            "filter": "circle:-0.1278,51.5074,500",
            "limit": 1,
            "apiKey": GEOAPIFY_API_KEY,
        },
        timeout=10,
    )
    r.raise_for_status()
    features = r.json().get("features", [])
    name = features[0]["properties"].get("name", "?") if features else "(none in range)"
    return f"Key valid — sample result: `{name}`"
results["Geoapify"] = check("Geoapify Places API", _check_geoapify)

# 7. Foursquare (optional — OSM + Geoapify are primary sources)
def _check_foursquare():
    import requests
    from modules.api_fetcher import FOURSQUARE_API_KEY
    if not FOURSQUARE_API_KEY:
        raise ValueError("FOURSQUARE_API_KEY not set in .env — set it to enable Foursquare as a 3rd venue source")
    r = requests.get(
        "https://places-api.foursquare.com/places/search",
        headers={
            "Authorization": f"Bearer {FOURSQUARE_API_KEY}",
            "X-Places-Api-Version": "2025-06-17",
            "Accept": "application/json",
        },
        params={"ll": "51.5074,-0.1278", "limit": 1},
        timeout=10,
    )
    if r.status_code == 401:
        raise ValueError(
            "401 Unauthorized — your key is a Legacy API Key, which only works with the old "
            "deprecated endpoint. Go to foursquare.com/developer → your project → "
            "generate a new Service API Key (not Legacy) for the Places API product."
        )
    if r.status_code == 403:
        raise ValueError("Access forbidden (403) — key may not have Places API access")
    r.raise_for_status()
    results_list = r.json().get("results", [])
    name = results_list[0].get("name", "?") if results_list else "(none in range)"
    return f"Service API key valid — sample result: `{name}`"
results["Foursquare"] = check("Foursquare Places API", _check_foursquare, optional=True)

st.markdown("---")

# ── Summary ───────────────────────────────────────────────────────────────────
st.markdown("### Summary")

# Optional checks: Photon and Foursquare
OPTIONAL_CHECKS = {"Photon", "Foursquare"}

critical_results = {k: v for k, v in results.items() if k not in OPTIONAL_CHECKS}
optional_results = {k: v for k, v in results.items() if k in OPTIONAL_CHECKS}

passed_critical = sum(1 for ok, _ in critical_results.values() if ok)
total_critical = len(critical_results)
passed_optional = sum(1 for ok, _ in optional_results.values() if ok)

if passed_critical == total_critical:
    st.success(
        f"✅ All {total_critical} critical checks passed. "
        f"Optional services: {passed_optional}/{len(optional_results)} available."
    )
else:
    failed = [name for name, (ok, _) in critical_results.items() if not ok]
    st.error(
        f"{passed_critical}/{total_critical} critical checks passed. "
        f"Fix these: **{', '.join(failed)}**"
    )
if passed_optional < len(optional_results):
    skipped = [name for name, (ok, _) in optional_results.items() if not ok]
    st.info(
        f"ℹ️ Optional services unavailable: **{', '.join(skipped)}** — "
        "venue search still works via OpenStreetMap + Geoapify."
    )
