"""
Offline RAGAS evaluation against the golden dataset.

Unlike the live per-query scoring in app.services.ragas_service (which only
computes reference-free metrics because a real user query has no ground
truth), this script has a `reference` answer for every example, so it
computes all 4 classic RAGAS metrics: faithfulness, answer_relevancy,
context_precision (with-reference variant), and context_recall.

Run from the backend/ directory — matches how the rest of the app is run
locally, so .env resolution and `app.services.*` imports behave identically:

    cd backend && python -m eval.run_ragas_eval

Flags:
  --dataset PATH   golden dataset JSON (default: eval/golden_dataset.json)
  --no-reindex     skip re-indexing the venue fixture (faster iteration)
  --cleanup        delete the eval OpenSearch collection after the run
  --limit N        only run the first N examples
  --only ID        only run the example with this id
  --output PATH    where to write the JSON report (default: eval/results/report_<timestamp>.json)
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.services import rag_service
from app.services.agents import orchestrator_agent
from app.services.ragas_service import _embeddings, _llm
from eval.index_fixture import (
    EVAL_COLLECTION_NAME,
    all_city_collections,
    city_collection_name,
    ensure_fixture_indexed,
)

_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
_RESULTS_DIR = Path(__file__).parent / "results"

_METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


# ── Pipeline runners ───────────────────────────────────────────────────────────

def _collections_for_example(example: dict) -> List[str]:
    city = example.get("city", "")
    if city:
        return [city_collection_name(city)]
    return all_city_collections()


def _run_rag_pipeline(example: dict) -> Tuple[str, List[str], Optional[List[str]]]:
    """Mirrors backend/app/api/ai.py's rag_chat() handler exactly (same capacity
    windowing, same n_per_collection), so this measures the real production
    pipeline rather than a looser approximation of it."""
    query = example["query"]
    history = example.get("chat_history") or []
    search_query = rag_service.rewrite_query_with_history(query, history)
    constraints = rag_service.parse_query_constraints(search_query)

    min_cap = constraints.get("capacity")
    max_cap = None
    if min_cap is not None:
        window = 150 if min_cap > 250 else (400 - min_cap)
        max_cap = min_cap + max(window, 0)

    docs = rag_service.query_with_smart_filters(
        _collections_for_example(example),
        search_query,
        n_per_collection=8,
        min_capacity=min_cap,
        max_capacity=max_cap,
        max_budget=constraints.get("budget"),
    )
    result = rag_service.generate_rag_response_json(query, docs, history)
    return result.get("answer", ""), docs, None


def _extract_contexts_from_tool_result(tool_name: str, result: dict) -> List[str]:
    texts: List[str] = []
    for key in ("results", "venues"):
        items = result.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("text"):
                    texts.append(item["text"])

    if tool_name == "find_catering_options" and not result.get("error"):
        texts.append(
            f"Venue: {result.get('venue_name', '')}. "
            f"In-house catering: {result.get('in_house_catering')}. "
            f"Profile: {result.get('profile')}. "
            f"External options found: {result.get('external_options_found', 0)}."
        )

    if tool_name == "search_venues_live":
        for v in result.get("venues", []):
            if isinstance(v, dict) and not v.get("text"):
                texts.append(
                    f"{v.get('name', '')} ({v.get('type', '')}) - capacity {v.get('capacity', '')}, "
                    f"{v.get('address', '')}"
                )

    return texts


def _run_agent_pipeline(example: dict) -> Tuple[str, List[str], Optional[List[str]]]:
    query = example["query"]
    history = example.get("chat_history") or []
    city = example.get("city", "")

    collection_name = city_collection_name(city) if city else EVAL_COLLECTION_NAME

    contexts: List[str] = []
    final_answer = ""
    agents_used: List[str] = []

    for event in orchestrator_agent.run(query, collection_name, city, history):
        etype = event.get("type")
        if etype == "tool_result":
            contexts.extend(_extract_contexts_from_tool_result(event.get("tool", ""), event.get("result") or {}))
        elif etype == "answer" and event.get("agent") == "orchestrator":
            data = event.get("data") or {}
            final_answer = data.get("answer", "")
            agents_used = data.get("agents_used", [])

    return final_answer, contexts, agents_used


# ── RAGAS scoring (with-reference variants — golden dataset has ground truth) ──

async def _score_with_reference(query: str, response: str, contexts: List[str], reference: str) -> dict:
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall

    llm = _llm()
    sample = SingleTurnSample(
        user_input=query,
        response=response,
        retrieved_contexts=contexts or [],
        reference=reference,
    )

    metrics = {
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=_embeddings()),
        "faithfulness": Faithfulness(llm=llm),
        "context_precision": LLMContextPrecisionWithReference(llm=llm),
        "context_recall": LLMContextRecall(llm=llm),
    }

    scores: dict = {}
    for name, metric in metrics.items():
        try:
            scores[name] = await metric.single_turn_ascore(sample)
        except Exception as exc:
            scores[name] = None
            scores[f"{name}_error"] = str(exc)
    return scores


# ── Independent (non-RAGAS) sanity checks ──────────────────────────────────────

def _keyword_check(contexts: List[str], expected: Optional[List[str]]) -> Optional[dict]:
    if not expected:
        return None
    joined = "\n".join(contexts).lower()
    hits = [kw for kw in expected if kw.lower() in joined]
    return {"hits": hits, "total": len(expected), "pass": len(hits) == len(expected)}


def _venue_name_check(contexts: List[str], expected: Optional[List[str]]) -> Optional[dict]:
    if expected is None:
        return None
    if not expected:
        return {"hits": [], "total": 0, "pass": True}  # edge case: expecting zero matches
    joined = "\n".join(contexts).lower()
    hits = [n for n in expected if n.lower() in joined]
    return {"hits": hits, "total": len(expected), "pass": len(hits) == len(expected)}


def _agents_used_check(agents_used: Optional[List[str]], expected: Optional[List[str]]) -> Optional[dict]:
    if not expected:
        return None
    if agents_used is None:
        return {"pass": False, "actual": None, "expected": expected}
    return {"pass": all(a in agents_used for a in expected), "actual": agents_used, "expected": expected}


# ── Report ──────────────────────────────────────────────────────────────────────

def _aggregate(results: List[dict]) -> dict:
    agg = {}
    for m in _METRIC_NAMES:
        values = [r["scores"].get(m) for r in results if r.get("scores") and isinstance(r["scores"].get(m), (int, float))]
        agg[m] = {
            "mean": round(sum(values) / len(values), 4) if values else None,
            "scored": len(values),
            "total": len(results),
        }
    return agg


def _print_summary(results: List[dict], aggregate: dict, output_path: Path) -> None:
    def fmt(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "  - "

    print("\n" + "=" * 78)
    print(f"{'ID':<36} {'Faith':>7} {'AnsRel':>7} {'CtxPrec':>8} {'CtxRec':>7}")
    print("-" * 78)
    for r in results:
        s = r.get("scores") or {}
        print(
            f"{r['id']:<36} {fmt(s.get('faithfulness')):>7} {fmt(s.get('answer_relevancy')):>7} "
            f"{fmt(s.get('context_precision')):>8} {fmt(s.get('context_recall')):>7}"
        )
    print("-" * 78)
    print("AGGREGATE (mean over scored examples):")
    for m, v in aggregate.items():
        mean = f"{v['mean']:.3f}" if v["mean"] is not None else "n/a"
        print(f"  {m:<20} mean={mean}  scored={v['scored']}/{v['total']}")
    print(f"\nFull report written to: {output_path}")
    print("=" * 78)


# ── Main ────────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    print(f"Indexing eval fixture into '{EVAL_COLLECTION_NAME}' (force={not args.no_reindex})...")
    chunk_count = ensure_fixture_indexed(force=not args.no_reindex)
    print(f"  {chunk_count} chunks indexed.\n")

    with open(args.dataset, encoding="utf-8") as f:
        dataset = json.load(f)

    if args.only:
        dataset = [e for e in dataset if e["id"] == args.only]
    if args.limit:
        dataset = dataset[: args.limit]

    results = []
    for example in dataset:
        t0 = time.time()
        print(f"Running {example['id']} ({example['pipeline']})...")
        try:
            if example["pipeline"] == "agent":
                answer, contexts, agents_used = _run_agent_pipeline(example)
            else:
                answer, contexts, agents_used = _run_rag_pipeline(example)
        except Exception as exc:
            print(f"  ERROR running pipeline: {exc}")
            results.append({
                "id": example["id"], "category": example["category"], "pipeline": example["pipeline"],
                "error": str(exc), "scores": {},
            })
            continue

        scores = asyncio.run(_score_with_reference(example["query"], answer, contexts, example["reference"]))

        row = {
            "id": example["id"],
            "category": example["category"],
            "pipeline": example["pipeline"],
            "query": example["query"],
            "answer": answer,
            "retrieved_contexts": contexts,
            "scores": scores,
            "keyword_check": _keyword_check(contexts, example.get("expected_context_keywords")),
            "venue_name_check": _venue_name_check(contexts, example.get("expected_venue_names")),
            "agents_used_check": _agents_used_check(agents_used, example.get("expected_agents_used")),
            "duration_s": round(time.time() - t0, 2),
        }
        results.append(row)
        print(f"  -> {scores}")

    _RESULTS_DIR.mkdir(exist_ok=True)
    output_path = (
        Path(args.output) if args.output
        else _RESULTS_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )

    aggregate = _aggregate(results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "aggregate": aggregate,
        "results": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    _print_summary(results, aggregate, output_path)

    if args.cleanup:
        rag_service.delete_collection(EVAL_COLLECTION_NAME)
        print(f"Deleted eval collection '{EVAL_COLLECTION_NAME}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the golden dataset through the real pipeline and score with RAGAS.")
    parser.add_argument("--dataset", default=str(_DATASET_PATH))
    parser.add_argument("--no-reindex", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--output", default=None)
    run(parser.parse_args())
