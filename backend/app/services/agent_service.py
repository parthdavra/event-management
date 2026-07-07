"""
Event Planning Agent facade — delegates to the multi-agent system in
app.services.agents (Orchestrator -> Venue Agent + Catering Agent).
"""

from typing import Dict, Generator, List, Optional

from app.services.agents import orchestrator_agent


def run_agent(
    query: str,
    collection_name: str,
    city: str = "",
    chat_history: Optional[List[Dict]] = None,
) -> Generator[Dict, None, None]:
    """
    Run the multi-agent event planning system. Yields trace events.
    Final event has type='answer' with the structured response.
    """
    yield from orchestrator_agent.run(query, collection_name, city, chat_history)
