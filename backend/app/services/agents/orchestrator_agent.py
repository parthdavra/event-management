"""
Lead Orchestrator Agent — delegates to the Venue Agent and Catering Agent
specialists and synthesizes their results into a final answer. Does not call
any raw tool directly; its only two "tools" are the specialist agents.
"""

import json
from typing import Dict, Generator, List, Optional

from app.core.config import get_settings
from app.services.agents import catering_agent, venue_agent

settings = get_settings()

AGENT_NAME = "orchestrator"

ORCH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "consult_venue_agent",
            "description": (
                "Consult the Venue Specialist Agent. Use for ANY question about the event brief, "
                "venues, capacity, or location — it has full access to the indexed knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask the Venue Agent, with all relevant context (guest count, city, etc.) included."},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consult_catering_agent",
            "description": (
                "Consult the Catering Specialist Agent. Use whenever the user asks about food, "
                "catering, restaurants, or dining for a specific venue. Include the venue name, "
                "event type and guest count in the question if known."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask the Catering Agent, including venue name and event details."},
                },
                "required": ["question"],
            },
        },
    },
]

_SYSTEM_PROMPT = """You are the Lead Event Planning Orchestrator. You do not answer questions
yourself — you delegate to two specialist agents and synthesize their findings:

- Venue Agent: knows the event brief and venue database (name, address, capacity, contact).
  Consult it for ANY question about the event, venues, or capacity.
- Catering Agent: finds food/catering options for a specific venue. Consult it whenever the
  user asks about food, catering, restaurants, or dining — after you know the venue name
  (ask the Venue Agent first if you don't have it yet).

DECISION RULES:
1. ALWAYS consult the Venue Agent first for any event/venue-related question.
2. If the user also asks about food/catering, consult the Catering Agent next, passing along
   the venue name and event details you learned from the Venue Agent (or from the user).
3. You may consult both agents, and may consult an agent more than once if you need follow-up
   information. Think step by step.
4. Never make up venue names, phone numbers, capacities, prices or caterer names — only report
   what the specialist agents returned.
5. CONVERSATION CONTEXT: You have access to the full conversation history. If the user refers
   to something mentioned earlier ("the second one", "its price", "that venue", "same place"),
   resolve the reference from the conversation history before delegating.

FINAL ANSWER FORMAT:
When you have enough information, output ONLY a JSON object with these exact fields:
{
  "answer": "<your complete markdown answer>",
  "sources_used": ["<venue, doc, or caterer name>", ...],
  "confidence": "high" | "medium" | "low",
  "query_interpretation": "<one sentence: how you understood the question>",
  "tools_used": ["<specialist agent names you consulted>"]
}
"""

MAX_ITERATIONS = 6


def run(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, Dict]:
    from app.services.rag_service import _openai_client

    client = _openai_client()
    chat_history = chat_history or []

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(chat_history[-20:])
    messages.append({"role": "user", "content": query})

    agents_used: List[str] = []
    yield {"type": "thinking", "agent": AGENT_NAME, "message": "Deciding which specialist agents to consult…"}

    for iteration in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            tools=ORCH_TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2500,
        )

        choice = response.choices[0]
        messages.append(choice.message)

        if choice.finish_reason == "tool_calls":
            for tc in choice.message.tool_calls:
                delegate_name = tc.function.name
                try:
                    delegate_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    delegate_args = {}
                question = delegate_args.get("question", query)

                if delegate_name == "consult_venue_agent":
                    target_agent = "venue_agent"
                    yield {"type": "delegate", "agent": AGENT_NAME, "to": target_agent, "question": question}
                    sub_result = yield from venue_agent.run(question, collection_name, city, chat_history=None)
                elif delegate_name == "consult_catering_agent":
                    target_agent = "catering_agent"
                    yield {"type": "delegate", "agent": AGENT_NAME, "to": target_agent, "question": question}
                    sub_result = yield from catering_agent.run(question, collection_name, city, chat_history=None)
                else:
                    target_agent = delegate_name
                    sub_result = {"error": f"Unknown specialist agent: {delegate_name}"}

                agents_used.append(target_agent)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(sub_result, default=str),
                })

            yield {"type": "thinking", "agent": AGENT_NAME, "message": f"Iteration {iteration + 1}: synthesising specialist findings…"}

        elif choice.finish_reason in ("stop", "length"):
            raw_content = choice.message.content or ""
            try:
                cleaned = raw_content.strip()
                if cleaned.startswith("```"):
                    cleaned = "\n".join(cleaned.split("\n")[1:])
                if cleaned.endswith("```"):
                    cleaned = "\n".join(cleaned.split("\n")[:-1])
                answer_data = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                answer_data = {
                    "answer": raw_content or "I was unable to generate a structured answer.",
                    "sources_used": [],
                    "confidence": "low",
                    "query_interpretation": query,
                    "tools_used": [],
                }
            answer_data.setdefault("tools_used", [])
            answer_data["tools_used"] = list(set(answer_data["tools_used"] + agents_used))
            answer_data["agents_used"] = list(dict.fromkeys(agents_used))
            yield {"type": "answer", "agent": AGENT_NAME, "data": answer_data}
            return answer_data
        else:
            yield {"type": "error", "agent": AGENT_NAME, "message": f"Unexpected finish_reason: {choice.finish_reason}"}
            return {
                "answer": "The orchestrator stopped unexpectedly.",
                "sources_used": [],
                "confidence": "low",
                "query_interpretation": query,
                "tools_used": agents_used,
                "agents_used": list(dict.fromkeys(agents_used)),
            }

    fallback = {
        "answer": "I reached the maximum number of reasoning steps. Please try rephrasing your question.",
        "sources_used": [],
        "confidence": "low",
        "query_interpretation": query,
        "tools_used": agents_used,
        "agents_used": list(dict.fromkeys(agents_used)),
    }
    yield {"type": "answer", "agent": AGENT_NAME, "data": fallback}
    return fallback
