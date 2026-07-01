"""
Event Planning Agent — decision-making core.

The agent has a reasoning loop powered by OpenAI tool-calling:
  1. Receives user query + chat history
  2. Decides WHICH tools to call (rag_search, filter_by_capacity, search_venues_live)
  3. Calls tools, reads results, decides whether to call more tools or answer
  4. Outputs a structured JSON answer

This is a GENERATOR — it yields trace events as it works so the UI
can show live "what the agent is doing" without waiting for the final answer.

Trace event shapes:
  {"type": "thinking",    "message": str}
  {"type": "tool_call",   "tool": str, "args": dict}
  {"type": "tool_result", "tool": str, "summary": str, "count": int}
  {"type": "answer",      "data": dict}
  {"type": "error",       "message": str}
"""

import json
from typing import Dict, Generator, List, Optional

from modules.tools import TOOL_REGISTRY

# ── Tool schema definitions sent to the LLM ───────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "Search the indexed knowledge base — contains the event brief document "
                "AND venue data (name, type, address, capacity, contact). "
                "Use this first for ANY question about the event or venues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The specific question or topic to search for in the index.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of chunks to retrieve (default 8, max 15).",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_by_capacity",
            "description": (
                "Scan the indexed venues and return ONLY those whose confirmed capacity "
                "is >= the requested minimum. "
                "Use when the user specifies a minimum number of guests / attendees."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "min_capacity": {
                        "type": "integer",
                        "description": "Minimum number of people the venue must accommodate.",
                    }
                },
                "required": ["min_capacity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_venues_live",
            "description": (
                "Search for venues in real-time from Geoapify / OpenStreetMap APIs. "
                "Use ONLY when the indexed data has too few results or the user explicitly "
                "asks to find new / additional venues not already in the index."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name to search in (e.g. 'London').",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Venue categories. Pick from: "
                            "Restaurants & Cafes, Bars & Nightlife, Hotels & Accommodation, "
                            "Conference & Event Venues, Arts & Entertainment, "
                            "Sports & Recreation, Attractions & Tourism."
                        ),
                    },
                    "radius_km": {
                        "type": "integer",
                        "description": "Search radius in kilometres (default 5).",
                        "default": 5,
                    },
                    "min_capacity": {
                        "type": "integer",
                        "description": "Optional: only return venues with at least this capacity.",
                    },
                },
                "required": ["city", "categories"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_catering_options",
            "description": (
                "Find food and catering options for an event at a specific venue. "
                "Checks whether the venue has its own in-house catering, then searches "
                "for external restaurants / caterers within walking distance. "
                "Auto-expands the search radius by 1 km if fewer than 3 options are found. "
                "Returns distance and walking time from the venue for every option. "
                "Use whenever the user asks about food, catering, restaurants, or dining for their event."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_name": {
                        "type": "string",
                        "description": "Exact name of the event venue.",
                    },
                    "event_type": {
                        "type": "string",
                        "description": (
                            "Type of event. Use one of: corporate, birthday, graduation, "
                            "wedding, conference, gala, networking, exhibition, "
                            "product_launch, party."
                        ),
                    },
                    "guest_count": {
                        "type": "integer",
                        "description": "Number of guests attending the event.",
                        "default": 50,
                    },
                    "prefer_external": {
                        "type": "boolean",
                        "description": "True if the user explicitly wants external catering even if venue has its own.",
                        "default": False,
                    },
                    "city": {
                        "type": "string",
                        "description": "City where the venue is located.",
                    },
                    "initial_radius_km": {
                        "type": "integer",
                        "description": "Starting search radius in km (default 1). Auto-expands if needed.",
                        "default": 1,
                    },
                },
                "required": ["venue_name", "event_type"],
            },
        },
    },
]

# ── Agent system prompt ───────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert event planning assistant with access to four tools.

DECISION RULES:
1. ALWAYS call rag_search first — it checks the indexed event brief and venue database.
2. If the user mentions a number of guests / capacity → ALSO call filter_by_capacity.
3. If the user asks about food, catering, restaurants, or dining → call find_catering_options
   with the venue name and the event type (corporate / birthday / graduation / wedding / etc.).
4. If rag_search returns fewer than 3 relevant venue results → consider search_venues_live.
5. You may call multiple tools before answering. Think step by step.
6. Never make up venue names, phone numbers, capacities or prices — only use what tools return.

CATERING RULES:
- Always report whether the venue has in-house catering (from find_catering_options result).
- List external options with their distance and walking time from the venue.
- If the radius was auto-expanded, tell the user what radius was needed to find options.
- Match food style to the event type (formal dinner for gala, casual buffet for birthday, etc.).

CAPACITY RULES:
- "confirmed" capacity = exact number from OSM / Geoapify data.
- "estimated" = typical range for the venue type. Flag clearly which is which.
- If filter_by_capacity returns 0 results, use rag_search results and note capacity is unconfirmed.

FINAL ANSWER FORMAT:
When you have enough information, output ONLY a JSON object with these exact fields:
{
  "answer": "<your complete markdown answer>",
  "sources_used": ["<venue or doc name>", ...],
  "confidence": "high" | "medium" | "low",
  "query_interpretation": "<one sentence: how you understood the question>",
  "tools_used": ["<tool names you called>"]
}
Do not output anything else after the JSON.
"""


# ── Agent class ───────────────────────────────────────────────────────────────

class EventPlanningAgent:
    """
    A tool-calling LLM agent for event planning queries.

    Usage:
        agent = EventPlanningAgent(collection_name="evp_u1_my_event")
        for event in agent.run(query, chat_history):
            if event["type"] == "answer":
                print(event["data"])
            else:
                print(event)  # trace event
    """

    MAX_ITERATIONS = 6

    def __init__(self, collection_name: str, city: str = ""):
        self.collection_name = collection_name
        self.city = city

    def run(
        self,
        query: str,
        chat_history: Optional[List[Dict]] = None,
    ) -> Generator[Dict, None, None]:
        """
        Generator that yields trace events while the agent reasons and calls tools.
        The final event has type="answer" and contains the structured response.
        """
        from modules.rag import _openai_client, AZURE_OPENAI_DEPLOYMENT

        client = _openai_client()
        chat_history = chat_history or []

        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        messages.extend(chat_history[-6:])
        messages.append({"role": "user", "content": query})

        tools_called: List[str] = []

        yield {"type": "thinking", "message": "Agent reasoning — choosing first tool…"}

        for iteration in range(self.MAX_ITERATIONS):
            response = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=2500,
            )

            choice = response.choices[0]
            messages.append(choice.message)

            # ── Agent decided to call tools ───────────────────────────────────
            if choice.finish_reason == "tool_calls":
                for tc in choice.message.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    # Inject collection_name / city where needed
                    if tool_name == "rag_search":
                        tool_args.setdefault("n_results", 8)
                        call_args = {"collection_name": self.collection_name, **tool_args}
                    elif tool_name == "filter_by_capacity":
                        call_args = {"collection_name": self.collection_name, **tool_args}
                    elif tool_name == "search_venues_live":
                        if self.city and "city" not in tool_args:
                            tool_args["city"] = self.city
                        call_args = tool_args
                    elif tool_name == "find_catering_options":
                        call_args = {"collection_name": self.collection_name, **tool_args}
                        if self.city and "city" not in call_args:
                            call_args["city"] = self.city
                    else:
                        call_args = tool_args

                    yield {"type": "tool_call", "tool": tool_name, "args": tool_args}

                    # Execute the tool
                    tool_fn = TOOL_REGISTRY.get(tool_name)
                    if tool_fn:
                        try:
                            result = tool_fn(**call_args)
                        except Exception as exc:
                            result = {"error": str(exc)}
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}

                    tools_called.append(tool_name)

                    # Build a short human-readable summary for the trace
                    summary = _summarise_result(tool_name, result)
                    count = (
                        result.get("chunks_found")
                        or result.get("matched_count")
                        or result.get("venues_found")
                        or 0
                    )
                    yield {
                        "type": "tool_result",
                        "tool": tool_name,
                        "summary": summary,
                        "count": count,
                        "result": result,
                    }

                    # Feed result back to the agent
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })

                yield {
                    "type": "thinking",
                    "message": f"Iteration {iteration + 1}: analysing tool results…",
                }

            # ── Agent decided it has enough info → output final answer ────────
            elif choice.finish_reason in ("stop", "length"):
                raw_content = choice.message.content or ""

                # Parse JSON answer
                try:
                    # Sometimes the LLM wraps JSON in a markdown code fence
                    cleaned = raw_content.strip()
                    if cleaned.startswith("```"):
                        cleaned = "\n".join(cleaned.split("\n")[1:])
                    if cleaned.endswith("```"):
                        cleaned = "\n".join(cleaned.split("\n")[:-1])
                    answer_data = json.loads(cleaned)
                except (json.JSONDecodeError, ValueError):
                    # Fallback: treat raw content as the answer text
                    answer_data = {
                        "answer": raw_content or "I was unable to generate a structured answer.",
                        "sources_used": [],
                        "confidence": "low",
                        "query_interpretation": query,
                        "tools_used": tools_called,
                    }

                answer_data.setdefault("tools_used", tools_called)
                answer_data["tools_used"] = list(set(answer_data["tools_used"] + tools_called))

                yield {"type": "answer", "data": answer_data}
                return

            else:
                # Unexpected finish reason
                yield {
                    "type": "error",
                    "message": f"Unexpected finish_reason: {choice.finish_reason}",
                }
                return

        # Hit max iterations
        yield {
            "type": "answer",
            "data": {
                "answer": "I reached the maximum number of reasoning steps. Please try rephrasing your question.",
                "sources_used": [],
                "confidence": "low",
                "query_interpretation": query,
                "tools_used": tools_called,
            },
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summarise_result(tool_name: str, result: Dict) -> str:
    if result.get("error"):
        return f"Error: {result['error']}"
    if tool_name == "rag_search":
        n = result.get("chunks_found", 0)
        types = {}
        for r in result.get("results", []):
            t = r.get("chunk_type", "doc")
            types[t] = types.get(t, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in types.items())
        return f"{n} chunks ({breakdown})"
    if tool_name == "filter_by_capacity":
        n = result.get("matched_count", 0)
        mn = result.get("min_capacity_requested", "?")
        return f"{n} venues with confirmed capacity ≥ {mn:,} people"
    if tool_name == "search_venues_live":
        n = result.get("venues_found", 0)
        city = result.get("city", "")
        return f"{n} venues found live in {city}"
    return str(result)
