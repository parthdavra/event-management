"""
RAGAS scoring for live chat queries.

Computed asynchronously on a background thread AFTER the response has already
been sent to the user — scoring involves several extra LLM calls per metric
(RAGAS decomposes the answer into claims and checks each against the context),
so doing it inline would add real latency and cost to every single query.

Only reference-free metrics are used, since a live user query has no
ground-truth answer to compare against:
  - Faithfulness           — is the answer grounded in the retrieved context?
  - Answer Relevancy       — is the answer relevant to the question asked?
  - Context Precision      — are the relevant retrieved chunks ranked higher?
    (LLMContextPrecisionWithoutReference variant — no reference needed)

Classic RAGAS Context Recall is NOT computed here: it requires a ground-truth
reference answer, which doesn't exist for a live query. That metric belongs to
the golden-dataset batch evaluation (a separate, still-pending piece of work).

Faithfulness and Context Precision also require retrieved_contexts, so they're
only meaningful for /ai/rag (the endpoint that actually does retrieval).
Answer Relevancy needs no context and is computed for every scored endpoint.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services import query_metrics_service

logger = logging.getLogger(__name__)
settings = get_settings()

_executor = ThreadPoolExecutor(max_workers=2)


def _llm():
    from langchain_openai import AzureChatOpenAI
    from ragas.llms import LangchainLLMWrapper
    return LangchainLLMWrapper(AzureChatOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_deployment,
    ))


def _embeddings():
    from langchain_openai import AzureOpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    return LangchainEmbeddingsWrapper(AzureOpenAIEmbeddings(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_embedding_deployment,
    ))


async def _score(query: str, response: str, contexts: Optional[List[str]]) -> dict:
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithoutReference

    llm = _llm()
    sample = SingleTurnSample(
        user_input=query,
        response=response,
        retrieved_contexts=contexts or [],
    )

    scores: dict = {}

    # RAGAS returns numpy.float64 — cast to plain float immediately so nothing
    # downstream (Postgres via psycopg2 in particular — it has no adapter for
    # numpy scalars and mis-serializes them) ever has to deal with the numpy type.
    try:
        scores["answer_relevancy"] = float(await AnswerRelevancy(
            llm=llm, embeddings=_embeddings()
        ).single_turn_ascore(sample))
    except Exception:
        logger.exception("RAGAS answer_relevancy scoring failed")

    if contexts:
        try:
            scores["faithfulness"] = float(await Faithfulness(llm=llm).single_turn_ascore(sample))
        except Exception:
            logger.exception("RAGAS faithfulness scoring failed")
        try:
            scores["context_precision"] = float(await LLMContextPrecisionWithoutReference(
                llm=llm
            ).single_turn_ascore(sample))
        except Exception:
            logger.exception("RAGAS context_precision scoring failed")

    return scores


def _score_and_save(query_metric_id: int, query: str, response: str, contexts: Optional[List[str]]) -> None:
    try:
        scores = asyncio.run(_score(query, response, contexts))
    except Exception:
        logger.exception("RAGAS scoring failed for query_metric_id=%s", query_metric_id)
        scores = {}

    db = SessionLocal()
    try:
        query_metrics_service.update_ragas_scores(db, query_metric_id, scores)
    except Exception:
        # This is the fire-and-forget tail of a background executor.submit() —
        # nobody ever calls .result() on the returned Future, so any exception
        # here would otherwise vanish completely silently. Must be caught and
        # logged here, not left to propagate.
        logger.exception("Failed to persist RAGAS scores for query_metric_id=%s", query_metric_id)
    finally:
        db.close()


def score_query_in_background(
    query_metric_id: int, query: str, response: str, contexts: Optional[List[str]] = None
) -> None:
    if not response:
        return
    _executor.submit(_score_and_save, query_metric_id, query, response, contexts)
