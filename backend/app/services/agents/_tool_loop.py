"""
Shared GPT tool-calling loop used by leaf/specialist agents (agents whose
tools are plain synchronous functions, as opposed to the orchestrator whose
"tools" are other agents).
"""

import json
from typing import Callable, Dict, Generator, List, Optional

from app.core.config import get_settings

settings = get_settings()


def run_tool_loop(
    agent_name: str,
    system_prompt: str,
    tool_definitions: List[Dict],
    tool_registry: Dict[str, Callable],
    build_call_args: Callable[[str, Dict], Dict],
    query: str,
    chat_history: Optional[List[Dict]] = None,
    max_iterations: int = 5,
    summarise_result: Optional[Callable[[str, Dict], str]] = None,
) -> Generator[Dict, None, Dict]:
    """
    Runs a single-agent tool-calling loop against Azure OpenAI. Yields trace
    events (each tagged with "agent": agent_name) and returns the final
    parsed answer dict once the model stops calling tools.
    """
    from app.services.rag_service import _openai_client

    client = _openai_client()
    chat_history = chat_history or []

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(chat_history[-20:])
    messages.append({"role": "user", "content": query})

    tools_called: List[str] = []
    yield {"type": "thinking", "agent": agent_name, "message": "Reasoning — choosing next tool…"}

    for iteration in range(max_iterations):
        response = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            tools=tool_definitions,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2500,
        )

        choice = response.choices[0]
        messages.append(choice.message)

        if choice.finish_reason == "tool_calls":
            for tc in choice.message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                call_args = build_call_args(tool_name, tool_args)

                yield {"type": "tool_call", "agent": agent_name, "tool": tool_name, "args": tool_args}

                tool_fn = tool_registry.get(tool_name)
                if tool_fn:
                    try:
                        result = tool_fn(**call_args)
                    except Exception as exc:
                        result = {"error": str(exc)}
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}

                tools_called.append(tool_name)

                summary = summarise_result(tool_name, result) if summarise_result else str(result)[:200]
                count = (
                    result.get("chunks_found") or
                    result.get("matched_count") or
                    result.get("venues_found") or 0
                )
                yield {
                    "type": "tool_result",
                    "agent": agent_name,
                    "tool": tool_name,
                    "summary": summary,
                    "count": count,
                    "result": result,
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

            yield {"type": "thinking", "agent": agent_name, "message": f"Iteration {iteration + 1}: analysing tool results…"}

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
                    "tools_used": tools_called,
                }
            answer_data.setdefault("tools_used", tools_called)
            answer_data["tools_used"] = list(set(answer_data["tools_used"] + tools_called))
            yield {"type": "answer", "agent": agent_name, "data": answer_data}
            return answer_data
        else:
            yield {"type": "error", "agent": agent_name, "message": f"Unexpected finish_reason: {choice.finish_reason}"}
            return {
                "answer": "The agent stopped unexpectedly.",
                "sources_used": [],
                "confidence": "low",
                "query_interpretation": query,
                "tools_used": tools_called,
            }

    fallback = {
        "answer": "I reached the maximum number of reasoning steps. Please try rephrasing your question.",
        "sources_used": [],
        "confidence": "low",
        "query_interpretation": query,
        "tools_used": tools_called,
    }
    yield {"type": "answer", "agent": agent_name, "data": fallback}
    return fallback
