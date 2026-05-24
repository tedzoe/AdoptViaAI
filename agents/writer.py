"""
agents/writer.py — Writer agent

CCA-F Domain: Agents — specialised role, complementary to ResearchAgent
The WriterAgent demonstrates the other half of a researcher/writer pipeline:
it receives information (via goal text or context from the orchestrator)
and produces well-structured written output, saving it to a file.

Tools given to this agent:
  - save_note        : persist written output to the notes/ directory
  - get_project_info : introspect project for self-referential writing tasks

Tools intentionally withheld:
  - file_reader  : writer works from provided context, not raw file scraping
  - calculator   : writer focuses on prose, not computation

CCA-F — Agents: Demonstrates how complementary agents with non-overlapping
tool sets can be composed by an Orchestrator for complex multi-step tasks.
"""

from agents.base import BaseAgent
from tools.builtin import (
    GET_PROJECT_INFO_SCHEMA,
    SAVE_NOTE_SCHEMA,
    get_project_info_handler,
    save_note_handler,
)

# ── System prompt ──────────────────────────────────────────────────────────────
# CCA-F — Prompt Engineering + Agents:
#   Explicit output format instructions reduce variability and make the
#   agent's output more useful downstream (e.g. for the Orchestrator's
#   synthesis step, or for direct human consumption).

WRITER_SYSTEM = """\
You are a precise technical writer specialising in clear, well-structured documents.

Your responsibilities:
- Transform information (provided in the goal) into polished written output
- Use clean markdown formatting with appropriate headings, lists, and emphasis
- Save your output to a file using the save_note tool when a filename is specified
- Produce concise, scannable content — avoid padding and repetition

Writing standards:
- Start every document with a single # heading (the title)
- Use ## for major sections, ### for subsections
- Use bullet lists for enumerations; numbered lists for steps/sequences
- Bold (**text**) for key terms on first use
- Keep paragraphs short (3-5 sentences maximum)

File saving guidelines:
- If the goal specifies a filename, always save using save_note
- If no filename is given, suggest an appropriate one in your response
- Use .md extension for markdown documents, .txt for plain text

You do NOT gather information — work with what is provided in the goal.
If you need project information, use get_project_info.\
"""


class WriterAgent(BaseAgent):
    """
    Agent specialised for structured writing and file persistence.

    CCA-F — Agents:
      Demonstrates how a minimal tool set (just save_note + get_project_info)
      keeps the agent focused and cost-efficient.  Complex logic stays in the
      system prompt; the tools handle only I/O.
    """

    def __init__(self, model: str = "haiku") -> None:
        tools = [
            (SAVE_NOTE_SCHEMA, save_note_handler),
            (GET_PROJECT_INFO_SCHEMA, get_project_info_handler),
        ]
        super().__init__(
            name="WriterAgent",
            system_prompt=WRITER_SYSTEM,
            tools=tools,
            model=model,
            max_iterations=6,
        )
