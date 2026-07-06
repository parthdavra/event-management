"""
RAG service — wraps Azure OpenAI + ChromaDB.
Uses HttpClient when CHROMA_USE_HTTP=true (Docker), PersistentClient for local dev.
"""

import json
from typing import Dict, Generator, List, Optional, Tuple

from app.core.config import get_settings

settings = get_settings()


# ── Client factories ──────────────────────────────────────────────────────────

def _openai_client():
    from openai import AzureOpenAI
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )


def _chroma_client():
    import chromadb
    if settings.chroma_use_http:
        return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    return chromadb.PersistentClient(path=settings.chroma_persist_dir)


# ── Collection helpers ────────────────────────────────────────────────────────

def get_collection(collection_name: str):
    return _chroma_client().get_or_create_collection(name=collection_name)


def delete_collection(collection_name: str) -> bool:
    try:
        _chroma_client().delete_collection(collection_name)
        return True
    except Exception:
        return False


# ── Embeddings ────────────────────────────────────────────────────────────────

def get_embeddings(texts: List[str]) -> List[List[float]]:
    client = _openai_client()
    response = client.embeddings.create(
        input=texts,
        model=settings.azure_openai_embedding_deployment,
    )
    return [item.embedding for item in response.data]


def add_to_collection(
    collection_name: str,
    texts: List[str],
    metadatas: List[dict],
    ids: List[str],
) -> int:
    collection = get_collection(collection_name)
    embeddings = get_embeddings(texts)
    collection.add(documents=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)
    return len(texts)


# ── Retrieval ─────────────────────────────────────────────────────────────────

def query_collection(
    collection_name: str, query: str, n_results: int = 5
) -> Tuple[List[str], List[dict]]:
    collection = get_collection(collection_name)
    query_embedding = get_embeddings([query])[0]
    results = collection.query(query_embeddings=[query_embedding], n_results=n_results)
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    return docs, metas


def query_multiple_collections(
    collection_names: List[str], query: str, n_per_collection: int = 3
) -> List[str]:
    all_docs: List[str] = []
    for name in collection_names:
        try:
            docs, _ = query_collection(name, query, n_per_collection)
            all_docs.extend(docs)
        except Exception:
            continue
    return all_docs[:10]


# ── Generation ────────────────────────────────────────────────────────────────

def generate_rag_response(
    query: str,
    context_docs: List[str],
    chat_history: Optional[List[dict]] = None,
) -> str:
    client = _openai_client()
    context = "\n\n---\n\n".join(context_docs) if context_docs else "No relevant context found."
    system_prompt = (
        "You are a helpful AI assistant for an event management platform. "
        "Use the retrieved context below to answer the user's question accurately. "
        "If the answer is not in the context, say so clearly and provide general guidance.\n\n"
        f"Retrieved Context:\n{context}"
    )
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-6:])
    messages.append({"role": "user", "content": query})
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=messages,
        temperature=0.3,
        max_tokens=1000,
    )
    return response.choices[0].message.content


def generate_rag_response_json(
    query: str,
    context_docs: List[str],
    chat_history: Optional[List[dict]] = None,
) -> dict:
    client = _openai_client()
    context = "\n\n---\n\n".join(context_docs) if context_docs else "No relevant context found."
    system_prompt = (
        "You are an expert AI assistant for an event management platform.\n"
        "Use the retrieved context below to answer the user's question accurately.\n\n"
        "CAPACITY HANDLING RULES:\n"
        "- If a venue has a confirmed capacity, state it explicitly.\n"
        "- If a venue has only an estimated range (marked 'Typical for a …'), use that range to assess fit.\n"
        "- When asked to find venues for N people, list ALL venues whose confirmed OR estimated capacity\n"
        "  is >= N (or whose range overlaps N), and clearly flag confirmed vs estimated.\n"
        "- Never refuse to answer a capacity question just because exact data is missing — use estimates.\n\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '  "answer"              : your complete answer as a markdown string\n'
        '  "sources_used"        : list of venue names or document titles you referenced\n'
        '  "confidence"          : "high" if context directly answers, "medium" if partial, "low" if not at all\n'
        '  "query_interpretation": one sentence describing how you understood the question\n\n'
        f"Retrieved Context:\n{context}"
    )
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-6:])
    messages.append({"role": "user", "content": query})
    resp = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=messages,
        temperature=0.3,
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def generate_chat_response(
    query: str, chat_history: Optional[List[dict]] = None
) -> str:
    client = _openai_client()
    system_prompt = (
        "You are a helpful AI assistant for an event management platform. "
        "Help users with event planning, scheduling, catering, venue selection, "
        "and any event-related questions. Be concise and practical."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-10:])
    messages.append({"role": "user", "content": query})
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=messages,
        temperature=0.7,
        max_tokens=800,
    )
    return response.choices[0].message.content


def extract_event_requirements(text: str) -> dict:
    """Use LLM to parse event requirements from a document or free-text input."""
    client = _openai_client()
    system_prompt = (
        "You are an event planning assistant. Extract structured requirements from the user's text.\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '- "event_name": string\n'
        '- "city": string (city name for geocoding, e.g. "London")\n'
        '- "location_hint": string (specific area, e.g. "Camden Market"; same as city if not mentioned)\n'
        '- "radius_km": number (search radius in km; use document value if stated, else 2)\n'
        '- "categories": array — choose from EXACTLY these values:\n'
        '  ["Restaurants & Cafes","Bars & Nightlife","Hotels & Accommodation",\n'
        '   "Conference & Event Venues","Arts & Entertainment","Sports & Recreation","Attractions & Tourism"]\n'
        "\n"
        "CATEGORY SELECTION RULES (follow strictly):\n"
        '- Corporate / networking / business / meeting / seminar / conference / AGM / product launch → ["Conference & Event Venues"]\n'
        '- Wedding / gala dinner / black-tie / formal banquet → ["Conference & Event Venues", "Hotels & Accommodation"]\n'
        '- Birthday / casual party / graduation / social → ["Restaurants & Cafes", "Bars & Nightlife"]\n'
        '- Concert / theatre / art show / exhibition / culture → ["Arts & Entertainment"]\n'
        '- Sports / fitness / team building → ["Sports & Recreation"]\n'
        "- Add 'Restaurants & Cafes' ONLY if the brief explicitly asks for a restaurant as the PRIMARY venue\n"
        "- NEVER return more than 2 categories unless the brief clearly spans multiple venue types\n"
        "\n"
        '- "guest_count": number or null\n'
        '- "budget": string or null (e.g. "£32,500")\n'
        '- "event_date": string or null\n'
        '- "event_type": string (concise description, e.g. "corporate networking evening")\n'
        '- "collection_slug": string (lowercase, max 20 chars, underscores only, e.g. "abc_corp_2026")'
    )
    resp = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text[:6000]},
        ],
        temperature=0,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)
