"""
core/conversation.py — Conversation history management

CCA-F Domain: Context Management (Domain 5)
Demonstrates:
  - Stateless API → stateful app: history must be passed on every call
  - Auto-summarisation to prevent context-window overflow
  - Token estimation for cost-aware UX
  - JSON persistence for session continuity

Phase 2 change: add_message() now accepts str | list content to support
tool_use and tool_result message blocks used in the agentic loop.
"""

import json
from pathlib import Path

# CCA-F — Context Management:
# 10 exchanges = 20 messages. Beyond this the context window grows
# linearly with every turn. Summarising old messages keeps input
# tokens (and cost) bounded.
SUMMARIZE_THRESHOLD = 20  # messages (10 user + 10 assistant)
KEEP_RECENT = 4           # always keep the last 2 exchanges verbatim


class ConversationManager:
    """
    Maintains the messages list that is passed to messages.create on every call.

    The Messages API is stateless — Claude has no memory between calls.
    This class provides the statefulness that makes multi-turn chat possible.

    Phase 2 note: `content` in add_message now supports both str (Phase 1)
    and list (Phase 2 tool_use / tool_result blocks).
    """

    def __init__(self) -> None:
        self._history: list[dict] = []

    # ── Message management ─────────────────────────────────────────────────────

    def add_message(self, role: str, content: str | list) -> None:
        """
        Append a message to the history.

        CCA-F Domain 4 — Tool Use:
          In tool-use conversations, content can be a list of content blocks
          (e.g. [{"type": "tool_use", ...}] or [{"type": "tool_result", ...}]).
          Accepting str | list keeps Phase 1 callers unchanged while supporting
          the richer format needed by the agentic loop.
        """
        self._history.append({"role": role, "content": content})

    def get_history(self) -> list[dict]:
        """Return a copy of the current message history."""
        return list(self._history)

    def replace_history(self, new_history: list[dict]) -> None:
        """
        Replace the full history (used after ToolExecutor updates the messages list).

        CCA-F Domain 4:
          After an agentic loop, the updated_messages returned by ToolExecutor
          contain all intermediate tool_use / tool_result pairs. This method
          lets the chat loop sync the ConversationManager to that state.
        """
        self._history = list(new_history)

    def clear(self) -> None:
        """Reset conversation history."""
        self._history = []

    def message_count(self) -> int:
        """Number of messages in history."""
        return len(self._history)

    # ── Token estimation ───────────────────────────────────────────────────────

    def get_token_estimate(self) -> int:
        """
        Rough estimate of tokens in the current history.

        CCA-F — Cost Management:
          Uses the ~4 characters per token heuristic (works well for
          English prose). Useful for dry-run estimates without making a
          separate count_tokens API call.
        """
        total_chars = 0
        for m in self._history:
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("content", "")))
                        total_chars += len(str(block.get("text", "")))
        return total_chars // 4

    # ── Auto-summarisation ─────────────────────────────────────────────────────

    def summarize_if_needed(self, client) -> bool:
        """
        Summarise old messages if history exceeds SUMMARIZE_THRESHOLD.

        CCA-F — Context Management:
          When history grows too long, we call Claude (with haiku for
          cost efficiency) to compress it. The summary replaces the old
          messages so every subsequent call stays within a manageable
          context window without losing conversational continuity.

        Returns True if summarisation occurred, False otherwise.
        """
        if len(self._history) < SUMMARIZE_THRESHOLD:
            return False

        # Only summarise messages with simple string content (skip tool blocks)
        to_compress = self._history[:-KEEP_RECENT]
        recent = self._history[-KEEP_RECENT:]

        # Build plain-text transcript (skip complex tool blocks in summary)
        transcript_parts = []
        for m in to_compress:
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                transcript_parts.append(f"{m['role'].upper()}: {content}")

        if not transcript_parts:
            return False   # nothing summarisable

        transcript = "\n".join(transcript_parts)
        summary_text = client.summarize(transcript)

        # Replace history: summary placeholder + recent verbatim messages
        self._history = [
            {
                "role": "user",
                "content": (
                    "[Previous conversation summary — treat this as established context]:\n"
                    + summary_text
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Understood. I have full context from the summarised conversation "
                    "and will continue from there."
                ),
            },
        ] + recent

        return True

    # ── Persistence ────────────────────────────────────────────────────────────

    def save_to_file(self, filename: str) -> None:
        """
        Save conversation history to a JSON file.

        CCA-F — Context Management:
          Persisting history enables resumable sessions — load the file
          in the next invocation with `avai chat --load` to continue
          exactly where you left off.
        """
        Path(filename).write_text(
            json.dumps(self._history, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_from_file(self, filename: str) -> None:
        """Load conversation history from a JSON file."""
        data = json.loads(Path(filename).read_text(encoding="utf-8"))
        if isinstance(data, list):
            self._history = data
        else:
            raise ValueError(f"Invalid conversation file format: {filename}")
