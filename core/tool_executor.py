"""
core/tool_executor.py — Agentic tool-use loop

CCA-F Domain 4: Agentic Loop Pattern
This is the heart of tool use. The loop:
  1. Sends messages + tool definitions to Claude
  2. If stop_reason == "tool_use": execute tools, append results, repeat
  3. If stop_reason == "end_turn": Claude finished — return the response
  4. Guard with max_iterations to prevent infinite loops

Key concepts demonstrated:
  - Tool call extraction from response content blocks
  - tool_result message format (fed back to Claude in next turn)
  - Multi-tool-call handling in a single response
  - Cost tracking across every loop iteration (each is a real API call)
  - Rich-formatted tool call indicators for live progress feedback
"""

import json
import time
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from core.client import ClaudeClient
    from core.cost_tracker import CostTracker
    from tools.registry import ToolRegistry

console = Console(legacy_windows=False)

# Sentinel returned when the loop exhausts max_iterations
_MAX_ITER_MSG = (
    "[Warning: agentic loop reached max_iterations limit — "
    "response may be incomplete]"
)


class ToolExecutor:
    """
    Implements the Claude agentic loop for tool-use conversations.

    CCA-F Domain 4 — Agentic Loop:
      The Messages API is fundamentally request/response. Tool use turns
      it into a loop:  ask → tool_use → tool_result → ask → … → end_turn.
      Each iteration is a separate API call with its own token charges.
      This class manages the loop so callers get back a single final string.
    """

    def __init__(self) -> None:
        self.iterations: int = 0          # number of API calls made in last execute()
        self.tool_calls_made: int = 0     # number of tool invocations

    def execute(
        self,
        client: "ClaudeClient",
        messages: list[dict],
        tools_registry: "ToolRegistry",
        tracker: "CostTracker",
        system_prompt: str,
        model: str,
        max_tokens: int,
        max_iterations: int = 10,
    ) -> tuple[str, list[dict]]:
        """
        Run the agentic loop until Claude returns end_turn or iterations run out.

        Args:
            client:         ClaudeClient instance (handles prompt caching)
            messages:       Current conversation history (will be extended in-place copy)
            tools_registry: Registered tool definitions + handlers
            tracker:        CostTracker — accumulates cost for every API call in loop
            system_prompt:  Stable system prompt (cached across iterations)
            model:          Active model shortname
            max_tokens:     Max output tokens per API call
            max_iterations: Safety limit on loop iterations

        Returns:
            (final_text, updated_messages) where updated_messages includes
            all intermediate tool_use / tool_result pairs from the loop.
            The final assistant text response is NOT in updated_messages —
            the caller should append it.
        """
        self.iterations = 0
        self.tool_calls_made = 0

        tool_definitions = tools_registry.get_definitions()
        current_messages = list(messages)   # work on a copy

        for _ in range(max_iterations):
            self.iterations += 1

            # ── API call (iteration N) ─────────────────────────────────────────
            # CCA-F Domain 4: Pass tool definitions on every call.
            # Claude decides whether to call tools or respond directly.
            response = client.send_message(
                messages=current_messages,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                tools=tool_definitions,
            )
            tracker.add_call(response.usage, model)

            stop_reason = response.stop_reason

            # ── End of conversation ────────────────────────────────────────────
            if stop_reason == "end_turn":
                final_text = _extract_text(response.content)
                return final_text, current_messages

            # ── Tool use requested ─────────────────────────────────────────────
            elif stop_reason == "tool_use":
                # CCA-F Domain 4: Convert SDK content blocks → dict format.
                # The assistant message must include both text and tool_use blocks
                # exactly as Claude produced them (including tool IDs).
                assistant_content_dicts = _content_blocks_to_dicts(response.content)
                current_messages.append(
                    {"role": "assistant", "content": assistant_content_dicts}
                )

                # Find all tool_use blocks in this response
                tool_use_blocks = [
                    b for b in response.content if b.type == "tool_use"
                ]

                tool_results = []
                for block in tool_use_blocks:
                    self.tool_calls_made += 1
                    result_dict = _invoke_tool(
                        block.name, block.input, tools_registry, tracker
                    )
                    # CCA-F Domain 4: tool_result message format.
                    # tool_use_id must match the id in the tool_use block above.
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result_dict, ensure_ascii=False),
                        }
                    )

                # Append all tool results as a single user message
                current_messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason (e.g. max_tokens hit mid-tool-loop)
                console.print(
                    f"[yellow]Unexpected stop_reason: '{stop_reason}' — ending loop.[/yellow]"
                )
                final_text = _extract_text(response.content)
                return final_text, current_messages

        # Exhausted max_iterations
        console.print(
            f"[yellow](!) Agentic loop reached {max_iterations} iterations -- stopping.[/yellow]"
        )
        return _MAX_ITER_MSG, current_messages


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_text(content_blocks) -> str:
    """Concatenate all text blocks from a response content list."""
    return "".join(
        getattr(b, "text", "") for b in content_blocks if b.type == "text"
    )


def _content_blocks_to_dicts(content_blocks) -> list[dict]:
    """
    Convert SDK content block objects to plain dicts for the messages list.

    CCA-F Domain 4:
      The Anthropic SDK returns typed objects (TextBlock, ToolUseBlock).
      When we append these back to the messages list for the next API call,
      we convert them to plain dicts to avoid any SDK version–specific
      serialisation behaviour.
    """
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return result


def _invoke_tool(
    tool_name: str,
    tool_inputs: dict,
    tools_registry: "ToolRegistry",
    tracker: "CostTracker",
) -> dict:
    """
    Execute a single tool call and display a Rich progress indicator.

    CCA-F Domain 4:
      Tool handlers run locally — no additional API calls.
      Results are JSON-serialised and returned to Claude in the next
      turn as a tool_result content block.
    """
    # Show the tool call indicator BEFORE executing
    input_preview = json.dumps(tool_inputs, ensure_ascii=False)
    if len(input_preview) > 60:
        input_preview = input_preview[:57] + "..."

    console.print(
        f"  [cyan]>>[/cyan] [bold]Tool:[/bold] [yellow]{tool_name}[/yellow]  "
        f"[dim]{input_preview}[/dim]",
        end="  ",
    )

    start = time.monotonic()
    try:
        handler = tools_registry.get_handler(tool_name)
        result = handler(tool_inputs)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        console.print(f"[green]OK[/green] [dim]({elapsed_ms}ms)[/dim]")
        tracker.log_tool_call(tool_name, tool_inputs, result, elapsed_ms)
        return result
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        error_result = {"error": str(exc)}
        console.print(f"[red]FAIL[/red] [dim]{exc}[/dim]")
        tracker.log_tool_call(tool_name, tool_inputs, error_result, elapsed_ms)
        return error_result
