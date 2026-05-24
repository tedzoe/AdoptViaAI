"""
agents/researcher.py — Research agent

CCA-F Domain: Agents — specialised role with curated tool set
The ResearchAgent demonstrates how a well-crafted system prompt
combined with the right tools produces focused, reliable agent behaviour.

Tools given to this agent:
  - file_reader      : read local source files and docs
  - calculator       : perform numeric analysis
  - get_project_info : introspect the AdoptviaAI project itself

Tools intentionally withheld:
  - save_note : this agent GATHERS information, not saves it
  The WriterAgent is responsible for persistence — separation of concerns.
"""

from agents.base import BaseAgent
from tools.builtin import (
    CALCULATOR_SCHEMA,
    FILE_READER_SCHEMA,
    GET_PROJECT_INFO_SCHEMA,
    calculator_handler,
    file_reader_handler,
    get_project_info_handler,
)

# ── System prompt ──────────────────────────────────────────────────────────────
# CCA-F — Prompt Engineering + Agents:
#   This prompt is STABLE (no dynamic content) so it benefits from
#   prompt caching across every iteration of the agentic loop.
#   The explicit methodology (decompose → gather → synthesize) guides
#   Claude toward reliable multi-step research behaviour.

RESEARCHER_SYSTEM = """\
You are a systematic research agent specialising in gathering and synthesising information.

Your methodology:
1. Decompose — break the research goal into specific sub-questions
2. Gather    — use available tools to collect relevant information
3. Synthesise — combine what you found into a clear, structured answer

Tool usage guidelines:
- Use get_project_info first when asked about this project's capabilities
- Use file_reader to examine specific files referenced in the goal
- Use calculator for any numeric computations needed during research
- Always cite where information came from (file path, tool used)

Output format:
- Lead with a brief direct answer to the goal
- Follow with supporting evidence organised under clear headings
- End with a "Sources" section listing which tools/files you used
- Be factual; if you cannot find information with available tools, say so clearly

You do NOT save files — report findings as text; the WriterAgent handles persistence.\
"""


class ResearchAgent(BaseAgent):
    """
    Agent specialised for information gathering and synthesis.

    CCA-F — Agents:
      Demonstrates how restricting a agent's tool set (no save_note)
      enforces the single-responsibility principle and makes the agent's
      behaviour more predictable and auditable.
    """

    def __init__(self, model: str = "haiku") -> None:
        tools = [
            (FILE_READER_SCHEMA, file_reader_handler),
            (CALCULATOR_SCHEMA, calculator_handler),
            (GET_PROJECT_INFO_SCHEMA, get_project_info_handler),
        ]
        super().__init__(
            name="ResearchAgent",
            system_prompt=RESEARCHER_SYSTEM,
            tools=tools,
            model=model,
            max_iterations=8,
        )
