"""
Vector store adapter — AWS OpenSearch backing the RAG pipeline.

The app previously created one physical vector-store collection per user
document, per city, and per event plan/type. OpenSearch has no lightweight
equivalent — one physical index per logical collection would risk shard
exhaustion on a single-node domain. Instead, everything lives in ONE shared index
(`settings.opensearch_index_name`) with `collection_name` stored as a
filterable keyword field, and every operation here implicitly filters on it.
"""

import uuid
from typing import Dict, List, Optional, Tuple

from opensearchpy import OpenSearch, RequestsHttpConnection, helpers

from app.core.config import get_settings

settings = get_settings()

EMBEDDING_DIM = 1536  # text-embedding-3-small

_KEYWORD_METADATA_FIELDS = [
    "source", "chunk_type", "venue_name", "venue_type", "api_source",
    "city", "event_type", "source_id", "has_canvas_data",
]
_TEXT_METADATA_FIELDS = ["phone", "email", "website", "capacity", "lat", "lon", "canvas_from_price"]


def _client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_auth=(settings.opensearch_username, settings.opensearch_password),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def ensure_index() -> None:
    client = _client()
    if client.indices.exists(index=settings.opensearch_index_name):
        return
    properties = {
        "id": {"type": "keyword"},
        "collection_name": {"type": "keyword"},
        "chunk_index": {"type": "integer"},
        "document": {"type": "text"},
        "embedding": {
            "type": "knn_vector",
            "dimension": EMBEDDING_DIM,
            "method": {
                "name": "hnsw",
                "engine": "nmslib",
                "space_type": "cosinesimil",
            },
        },
    }
    for field in _KEYWORD_METADATA_FIELDS:
        properties[field] = {"type": "keyword"}
    for field in _TEXT_METADATA_FIELDS:
        properties[field] = {"type": "keyword"}

    client.indices.create(
        index=settings.opensearch_index_name,
        body={
            "settings": {"index.knn": True},
            "mappings": {"properties": properties},
        },
    )


def _doc_id(collection_name: str, chunk_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{collection_name}::{chunk_id}").hex


def _build_where_filter(collection_name: str, where: Optional[dict]) -> List[dict]:
    filters = [{"term": {"collection_name": collection_name}}]
    for field, condition in (where or {}).items():
        value = condition["$eq"] if isinstance(condition, dict) and "$eq" in condition else condition
        filters.append({"term": {field: value}})
    return filters


def _hit_to_doc_meta(source: dict) -> Tuple[str, dict]:
    document = source.pop("document", "")
    source.pop("embedding", None)
    source.pop("collection_name", None)
    source.pop("id", None)
    return document, source


# ── Public API used by rag_service.py ──────────────────────────────────────

def add(
    collection_name: str,
    ids: List[str],
    documents: List[str],
    embeddings: List[List[float]],
    metadatas: List[dict],
) -> int:
    ensure_index()
    client = _client()
    actions = []
    for chunk_id, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
        body = {"id": chunk_id, "collection_name": collection_name, "document": document, "embedding": embedding}
        body.update(metadata)
        actions.append({
            "_index": settings.opensearch_index_name,
            "_id": _doc_id(collection_name, chunk_id),
            "_source": body,
        })
    helpers.bulk(client, actions)
    return len(actions)


def query(collection_name: str, query_embedding: List[float], n_results: int = 5) -> Tuple[List[str], List[dict]]:
    ensure_index()
    client = _client()
    body = {
        "size": n_results,
        "query": {
            "bool": {
                "filter": [{"term": {"collection_name": collection_name}}],
                "must": [{"knn": {"embedding": {"vector": query_embedding, "k": n_results}}}],
            }
        },
    }
    try:
        resp = client.search(index=settings.opensearch_index_name, body=body)
    except Exception:
        return [], []
    docs, metas = [], []
    for hit in resp.get("hits", {}).get("hits", []):
        doc, meta = _hit_to_doc_meta(dict(hit["_source"]))
        docs.append(doc)
        metas.append(meta)
    return docs, metas


def get_by_filter(
    collection_name: str,
    where: Optional[dict] = None,
    include: Optional[List[str]] = None,
    limit: int = 2000,
) -> Tuple[List[str], List[dict]]:
    ensure_index()
    client = _client()
    body = {
        "size": limit,
        "query": {"bool": {"filter": _build_where_filter(collection_name, where)}},
    }
    try:
        resp = client.search(index=settings.opensearch_index_name, body=body)
    except Exception:
        return [], []
    docs, metas = [], []
    for hit in resp.get("hits", {}).get("hits", []):
        doc, meta = _hit_to_doc_meta(dict(hit["_source"]))
        docs.append(doc)
        metas.append(meta)
    return docs, metas


def delete_collection(collection_name: str) -> bool:
    ensure_index()
    client = _client()
    try:
        client.delete_by_query(
            index=settings.opensearch_index_name,
            body={"query": {"term": {"collection_name": collection_name}}},
        )
        return True
    except Exception:
        return False


def cluster_health() -> Dict:
    return _client().cluster.health()
