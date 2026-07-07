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

def get_raw_json_chunks_from_collection(
    collection_name: str, city: str = ""
) -> List[dict]:
    """
    Retrieve all raw-JSON venue chunks from a ChromaDB collection without
    a semantic query.  Used by Smart Planner to serve data from indexed chunks.
    Returns parsed venue dicts; empty list when collection doesn't exist.
    """
    import json as _json
    try:
        client = _chroma_client()
        try:
            col = client.get_collection(collection_name)
        except Exception:
            return []

        results = col.get(
            where={"chunk_type": {"$eq": "raw_json"}},
            include=["documents", "metadatas"],
        )
        docs: List[str] = results.get("documents") or []
        metas: List[dict] = results.get("metadatas") or []
        city_lower = city.lower().strip()

        venues: List[dict] = []
        for doc, meta in zip(docs, metas):
            if city_lower:
                chunk_city = (meta.get("city") or "").lower()
                if chunk_city and city_lower not in chunk_city and chunk_city not in city_lower:
                    continue
            try:
                nl_idx = doc.index("\n")
                venue = _json.loads(doc[nl_idx + 1:])
                venues.append(venue)
            except Exception:
                continue

        # If city filter left nothing, fall back to all venues in the collection
        if city_lower and not venues:
            for doc in docs:
                try:
                    nl_idx = doc.index("\n")
                    venue = _json.loads(doc[nl_idx + 1:])
                    venues.append(venue)
                except Exception:
                    continue

        return venues
    except Exception:
        return []


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


def parse_query_constraints(query: str) -> dict:
    """
    Regex-extract structured constraints from a user query:
      capacity (int): number of people / guests / seats requested
      budget (float): monetary budget in GBP
    Returns dict with keys 'capacity' and 'budget', both may be None.
    """
    import re as _re

    capacity = None
    cap_patterns = [
        r'(\d+)\s*(?:people|guests?|pax|seats?|seating\s+capacity|seating|attendees?|persons?)',
        r'(?:capacity|seating)\s+(?:of\s+|for\s+)?(\d+)',
        r'(?:accommodate|for)\s+(\d+)',
    ]
    for pat in cap_patterns:
        m = _re.search(pat, query, _re.IGNORECASE)
        if m:
            capacity = int(m.group(1))
            break

    budget = None
    budget_patterns = [
        r'budget\s+(?:is\s+|of\s+)?[£$]?\s*([\d,]+)',
        r'[£$]\s*([\d,]+)',
        r'([\d,]+)\s*pounds?\s*(?:budget)?',
    ]
    for pat in budget_patterns:
        m = _re.search(pat, query, _re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            val = float(raw)
            # Ignore implausibly small numbers (e.g. "2 pounds" is not a venue budget)
            if val >= 100:
                budget = val
                break

    return {"capacity": capacity, "budget": budget}


def query_with_smart_filters(
    collection_names: List[str],
    query: str,
    n_per_collection: int = 8,
    min_capacity: Optional[int] = None,
    max_capacity: Optional[int] = None,
    max_budget: Optional[float] = None,
) -> List[str]:
    """
    Enhanced retrieval combining semantic search with metadata-based capacity filtering.
    Capacity-matched venue chunks are ranked first; semantic results fill the remainder.
    Returns up to 15 deduplicated doc strings.
    """
    import re as _re

    # 1. Semantic search (existing path)
    semantic_docs: List[str] = []
    for name in collection_names:
        try:
            docs, _ = query_collection(name, query, n_per_collection)
            semantic_docs.extend(docs)
        except Exception:
            continue

    # 2. Metadata-based capacity filter (when a specific capacity is requested)
    capacity_docs: List[str] = []
    if min_capacity is not None:
        def _parse_cap(raw: str) -> int:
            nums = _re.findall(r"\d+", raw or "")
            return int(nums[0]) if nums else 0

        for name in collection_names:
            try:
                col = _chroma_client().get_collection(name)
                raw_results = col.get(
                    where={"chunk_type": "venue"},
                    include=["documents", "metadatas"],
                )
                for doc, meta in zip(
                    raw_results.get("documents", []),
                    raw_results.get("metadatas", []),
                ):
                    # Try metadata capacity first
                    cap = _parse_cap(meta.get("capacity", ""))
                    # If metadata empty, try parsing from doc text
                    if cap == 0:
                        m = _re.search(r'Capacity:\s*(?:~\s*)?(\d+)', doc, _re.IGNORECASE)
                        if m:
                            cap = int(m.group(1))
                    if cap == 0:
                        continue
                    if cap < min_capacity:
                        continue
                    if max_capacity is not None and cap > max_capacity:
                        continue
                    capacity_docs.append(doc)
            except Exception:
                continue

    # 3. Budget post-filter on semantic docs: drop venues clearly over budget
    if max_budget is not None and semantic_docs:
        filtered: List[str] = []
        for doc in semantic_docs:
            m = _re.search(r'(?:From|Price|Rate|Hire):\s*[£$]?\s*([\d,]+)', doc, _re.IGNORECASE)
            if m:
                price = float(m.group(1).replace(",", ""))
                if price > max_budget * 1.25:  # 25% tolerance to avoid dropping borderline venues
                    continue
            filtered.append(doc)
        semantic_docs = filtered

    # 4. Merge: capacity-matched first (highest relevance), semantic results fill remainder
    seen: set = set()
    merged: List[str] = []
    for doc in capacity_docs + semantic_docs:
        key = doc[:80]
        if key not in seen:
            seen.add(key)
            merged.append(doc)

    return merged[:15]


# ── Generation ────────────────────────────────────────────────────────────────

def rewrite_query_with_history(query: str, history: Optional[List[dict]]) -> str:
    """
    If the conversation has history, rewrite the query to be self-contained so
    ChromaDB vector search can resolve pronouns and references like "the second one",
    "its price", "that venue", etc.
    Returns the original query unchanged when there is no history or the call fails.
    """
    if not history or len(history) < 2:
        return query
    client = _openai_client()
    ctx = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}"
        for m in history[-8:]
    )
    try:
        resp = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a query rewriter. Given a conversation and the user's latest message, "
                        "rewrite the message so it is fully self-contained — resolve any pronouns, "
                        "ordinal references ('the second one', 'it', 'that venue', 'same place'), or "
                        "implicit context into explicit terms from the conversation.\n"
                        "If the message is already self-contained, return it UNCHANGED.\n"
                        "Output ONLY the rewritten query — no explanation, no preamble."
                    ),
                },
                {"role": "user", "content": f"Conversation:\n{ctx}\n\nLatest message: {query}"},
            ],
            temperature=0,
            max_tokens=200,
        )
        rewritten = resp.choices[0].message.content.strip()
        return rewritten or query
    except Exception:
        return query


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
        messages.extend(chat_history[-20:])
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
        "Use the retrieved context below AND the conversation history to answer the user's question.\n\n"
        "CAPACITY RULES (STRICT):\n"
        "- When the user asks for a venue for N people:\n"
        "  • ONLY recommend venues whose capacity is >= N.\n"
        "  • EXCLUDE venues whose capacity is larger than N + 150 (avoid oversized venues).\n"
        "    Example: user asks for 250 people → include venues with capacity 250-400, exclude 600+ capacity.\n"
        "  • If a capacity says 'up to X' or 'max X', use X for comparison.\n"
        "  • Always state each venue's exact capacity in your answer.\n"
        "  • If NO venues in the context match this range, say 'No venues found for that capacity' — do NOT invent venues.\n\n"
        "BUDGET RULES (STRICT):\n"
        "- When the user states a budget (e.g. £5,000):\n"
        "  • Highlight venues whose hire fee / starting price is at or below the budget.\n"
        "  • Mark venues above the stated budget with '⚠️ Over budget'.\n"
        "  • If pricing is not stated for a venue, note 'Price: contact venue' and include it.\n"
        "  • Never omit an on-budget venue just because another field is missing.\n\n"
        "AREA / LOCATION RULES (STRICT):\n"
        "- When the user names a specific area, neighbourhood, or postcode:\n"
        "  • ONLY list venues in or immediately adjacent to that area.\n"
        "  • If no venues are found in the requested area, say so explicitly and suggest the nearest match.\n"
        "  • Do NOT list venues in other cities or far-away boroughs as alternatives unless no local match exists.\n\n"
        "CONVERSATION CONTEXT RULES:\n"
        "- If the user refers to something mentioned earlier ('the second one', 'its price', 'that venue'),\n"
        "  resolve the reference from the conversation history before answering.\n"
        "- Maintain continuity across turns — do not forget what was discussed.\n\n"
        "GENERAL:\n"
        "- If the context is insufficient to answer with confidence, say so clearly. Do NOT hallucinate venues.\n"
        "- Format your answer in clean markdown with venue details as a bulleted or numbered list.\n\n"
        "Return ONLY a valid JSON object with exactly these fields:\n"
        '  "answer"              : your complete answer as a markdown string\n'
        '  "sources_used"        : list of venue names or document titles you referenced\n'
        '  "confidence"          : "high" if context directly answers, "medium" if partial, "low" if not at all\n'
        '  "query_interpretation": one sentence describing how you understood the question\n\n'
        f"Retrieved Context:\n{context}"
    )
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-20:])
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
        "and any event-related questions. Be concise and practical. "
        "Maintain full conversation context across turns."
    )
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        messages.extend(chat_history[-20:])
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


def parse_catering_requirements(text: str) -> dict:
    """Use LLM to extract catering dietary groups, headcount, budget, and location from free text."""
    try:
        client = _openai_client()
        system_prompt = (
            "You are a catering requirements parser. Extract structured catering information from the user's text.\n"
            "Return ONLY a valid JSON object with exactly these fields:\n"
            '- "groups": array of objects, each with:\n'
            '    - "label": string (descriptive name, e.g. "Vegan Guests")\n'
            '    - "count": integer (number of people in this group)\n'
            '    - "dietary_type": string — MUST be exactly one of: vegan, vegetarian, halal, kosher, non-veg, gluten-free\n'
            "    RULES for dietary_type:\n"
            "    - 'halal non-veg', 'non-veg halal', 'halal meat' → 'halal'\n"
            "    - 'vegetarian', 'veg' → 'vegetarian'\n"
            "    - 'vegan' → 'vegan'\n"
            "    - 'regular', 'standard', 'no restriction', 'non-vegetarian', 'non veg', 'normal' → 'non-veg'\n"
            "    - 'kosher' → 'kosher'\n"
            "    - 'gluten free', 'gluten-free', 'celiac' → 'gluten-free'\n"
            '- "total_headcount": integer (sum of all group counts; if not stated, sum the groups)\n'
            '- "budget": number or null (total food budget in GBP; strip currency symbols; null if not mentioned)\n'
            '- "location": string or null (city name for vendor search; null if not mentioned)\n'
            "If no dietary groups are found, return an empty groups array."
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
    except Exception:
        return {"groups": [], "total_headcount": 0, "budget": None, "location": None}
