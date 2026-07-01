"""
Input & output guardrails for the Smart Planner chat.

Input pipeline:
  - Abuse / hate-speech detection  → reject with a polite message
  - Spell correction                → fix typos, preserve meaning
  - Sarcasm detection               → extract the real intent

Output pipeline:
  - Enforce JSON schema             → fill missing fields with safe defaults
  - Confidence validation           → clamp to high / medium / low
  - Source-list sanitisation        → ensure it is always a list
"""

import json
from typing import Dict

ABUSE_MESSAGE = (
    "I'm here to help with event planning — "
    "venues, catering, logistics, budgets. "
    "Please keep the conversation respectful and I'll do my best to help."
)

_INPUT_SYSTEM = """You are a safety and query-preprocessing assistant for an event management chatbot.

Analyse the user query and return ONLY a JSON object with these exact fields:

{
  "allowed": true | false,
  "rejection_reason": null | "short reason string",
  "corrected_query": "spell-corrected version (fix typos, keep meaning)",
  "was_corrected": true | false,
  "corrections": [{"original": "venu", "corrected": "venue"}, ...],
  "is_sarcastic": true | false,
  "sarcasm_explanation": null | "what the sarcasm means",
  "real_intent": "the actual question the user is asking",
  "category": "venue_search" | "event_planning" | "pricing" | "catering" | "logistics" | "general" | "off_topic" | "abusive"
}

RULES
- allowed = false ONLY for: profanity, hate speech, personal attacks, sexual content, violence, illegal activity.
- Allow all event-related queries even if oddly or imperfectly phrased.
- Correct obvious typos: "restrant"→"restaurant", "venu"→"venue", "capasity"→"capacity", etc.
- Sarcasm examples:
    "Oh great, so you STILL can't find a decent venue?" → real_intent: "Find suitable venue options"
    "Wow, such amazing budget advice" → real_intent: "Give me budget advice for my event"
- Off-topic queries: allowed=true, category="off_topic", real_intent unchanged.
"""

_OUTPUT_REQUIRED_KEYS = {"answer", "sources_used", "confidence", "query_interpretation"}
_VALID_CONFIDENCE = {"high", "medium", "low"}


def check_input(query: str) -> Dict:
    """
    Run input guardrails on a user query.

    Returns a dict with:
      allowed           bool
      rejection_reason  str | None
      corrected_query   str
      was_corrected     bool
      corrections       list[dict]
      is_sarcastic      bool
      sarcasm_explanation str | None
      real_intent       str   ← use this as the actual query for RAG
      category          str
    """
    from modules.rag import _openai_client, AZURE_OPENAI_DEPLOYMENT

    client = _openai_client()
    resp = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _INPUT_SYSTEM},
            {"role": "user", "content": query},
        ],
        temperature=0,
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content)

    # Ensure all expected keys exist with safe defaults
    result.setdefault("allowed", True)
    result.setdefault("rejection_reason", None)
    result.setdefault("corrected_query", query)
    result.setdefault("was_corrected", False)
    result.setdefault("corrections", [])
    result.setdefault("is_sarcastic", False)
    result.setdefault("sarcasm_explanation", None)
    result.setdefault("real_intent", result.get("corrected_query", query))
    result.setdefault("category", "general")

    return result


def validate_output(raw: dict) -> dict:
    """
    Validate and sanitise a structured LLM JSON response.
    Fills missing fields with safe defaults so callers never KeyError.
    """
    raw.setdefault("answer", "No answer was generated.")
    raw.setdefault("sources_used", [])
    raw.setdefault("confidence", "low")
    raw.setdefault("query_interpretation", "")

    if raw["confidence"] not in _VALID_CONFIDENCE:
        raw["confidence"] = "low"

    if not isinstance(raw["sources_used"], list):
        raw["sources_used"] = [str(raw["sources_used"])]

    if not isinstance(raw["answer"], str):
        raw["answer"] = str(raw["answer"])

    return raw
