"""
RAG service — wraps Azure OpenAI + AWS OpenSearch (via app.services.vector_store).
"""

import json
from typing import Dict, Generator, List, Optional, Tuple

from app.core.config import get_settings
from app.prompts.chat_direct import PROMPT as _CHAT_DIRECT_PROMPT
from app.prompts.extract_requirements import PROMPT as _EXTRACT_REQUIREMENTS_PROMPT
from app.prompts.parse_catering_requirements import PROMPT as _PARSE_CATERING_PROMPT
from app.prompts.query_rewrite import PROMPT as _QUERY_REWRITE_PROMPT
from app.prompts.rag_answer_json import PROMPT_TEMPLATE as _RAG_ANSWER_JSON_TEMPLATE
from app.prompts.rag_answer_plain import PROMPT_TEMPLATE as _RAG_ANSWER_PLAIN_TEMPLATE

settings = get_settings()

if settings.langfuse_public_key and settings.langfuse_secret_key:
    from langfuse import Langfuse
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )


# ── Client factories ──────────────────────────────────────────────────────────

def _instrument_client(client):
    """Wrap chat/embeddings .create() to push token-usage metrics to CloudWatch,
    without touching any of the many call sites that use _openai_client()."""
    from app.services import metrics_service

    orig_chat_create = client.chat.completions.create
    orig_embed_create = client.embeddings.create

    def chat_create(*args, **kwargs):
        resp = orig_chat_create(*args, **kwargs)
        try:
            usage = getattr(resp, "usage", None)
            if usage:
                metrics_service.record_llm_usage(
                    model=kwargs.get("model", ""),
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )
        except Exception:
            pass
        return resp

    def embed_create(*args, **kwargs):
        resp = orig_embed_create(*args, **kwargs)
        try:
            usage = getattr(resp, "usage", None)
            if usage:
                metrics_service.record_llm_usage(
                    model=kwargs.get("model", ""),
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=0,
                )
        except Exception:
            pass
        return resp

    client.chat.completions.create = chat_create
    client.embeddings.create = embed_create
    return client


def _openai_client():
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        from langfuse.openai import AzureOpenAI
    else:
        from openai import AzureOpenAI
    client = AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )
    return _instrument_client(client)


# ── Collection helpers ────────────────────────────────────────────────────────

def delete_collection(collection_name: str) -> bool:
    from app.services import vector_store
    return vector_store.delete_collection(collection_name)


def get_chunks_by_filter(
    collection_name: str, where: Optional[dict] = None, include: Optional[List[str]] = None
) -> Tuple[List[str], List[dict]]:
    from app.services import vector_store
    return vector_store.get_by_filter(collection_name, where=where, include=include)


# ── Embeddings ────────────────────────────────────────────────────────────────

def get_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Azure/OpenAI embeddings enforce a hard 300k-token-per-request ceiling.
    Batch conservatively (~3 chars/token estimate — safe margin under the
    limit even for dense text) so bulk indexing many/large chunks (e.g.
    enriched venue rich-text + raw-JSON pairs) never blows past it in one call.
    """
    client = _openai_client()
    model = settings.azure_openai_embedding_deployment

    _MAX_CHARS_PER_BATCH = 450_000  # ~150k tokens at ~3 chars/token
    _MAX_ITEMS_PER_BATCH = 500

    all_embeddings: List[List[float]] = []
    batch: List[str] = []
    batch_chars = 0

    def _flush():
        nonlocal batch, batch_chars
        if not batch:
            return
        response = client.embeddings.create(input=batch, model=model)
        all_embeddings.extend(item.embedding for item in response.data)
        batch = []
        batch_chars = 0

    for text in texts:
        if batch and (batch_chars + len(text) > _MAX_CHARS_PER_BATCH or len(batch) >= _MAX_ITEMS_PER_BATCH):
            _flush()
        batch.append(text)
        batch_chars += len(text)
    _flush()

    return all_embeddings


def add_to_collection(
    collection_name: str,
    texts: List[str],
    metadatas: List[dict],
    ids: List[str],
) -> int:
    from app.services import vector_store
    embeddings = get_embeddings(texts)
    return vector_store.add(collection_name, ids, texts, embeddings, metadatas)


# ── Retrieval ─────────────────────────────────────────────────────────────────

def get_raw_json_chunks_from_collection(
    collection_name: str, city: str = ""
) -> List[dict]:
    """
    Retrieve all raw-JSON venue chunks from an OpenSearch collection without
    a semantic query.  Used by Smart Planner to serve data from indexed chunks.
    Returns parsed venue dicts; empty list when collection doesn't exist.
    """
    import json as _json
    from app.services import vector_store
    try:
        docs, metas = vector_store.get_by_filter(
            collection_name,
            where={"chunk_type": {"$eq": "raw_json"}},
            include=["documents", "metadatas"],
        )
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
    from app.services import vector_store
    query_embedding = get_embeddings([query])[0]
    return vector_store.query(collection_name, query_embedding, n_results=n_results)


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

        from app.services import vector_store
        for name in collection_names:
            try:
                docs_raw, metas_raw = vector_store.get_by_filter(
                    name,
                    where={"chunk_type": "venue"},
                    include=["documents", "metadatas"],
                )
                for doc, meta in zip(docs_raw, metas_raw):
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
    OpenSearch vector search can resolve pronouns and references like "the second one",
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
                {"role": "system", "content": _QUERY_REWRITE_PROMPT},
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
    system_prompt = _RAG_ANSWER_PLAIN_TEMPLATE.format(context=context)
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
    system_prompt = _RAG_ANSWER_JSON_TEMPLATE.format(context=context)
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
    messages = [{"role": "system", "content": _CHAT_DIRECT_PROMPT}]
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
    resp = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        messages=[
            {"role": "system", "content": _EXTRACT_REQUIREMENTS_PROMPT},
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
        resp = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=[
                {"role": "system", "content": _PARSE_CATERING_PROMPT},
                {"role": "user", "content": text[:6000]},
            ],
            temperature=0,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {"groups": [], "total_headcount": 0, "budget": None, "location": None}
