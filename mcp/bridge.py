"""
mcp/bridge.py — Connect MCP tools to the Claude API agentic loop

CCA-F Domain: MCP — Model Context Protocol
The key pattern this demonstrates: DYNAMIC TOOL DISCOVERY.

Phase 2 (hardcoded tools):
  tool definitions are written in Python at dev time and
  registered in the tool registry at startup.

Phase 4 (MCP bridge):
  tool definitions are fetched from the MCP server at RUNTIME.
  Claude receives the same Anthropic tool format — it doesn't
  know or care whether tools came from hardcode or MCP.

This decoupling means:
  - Tool servers can be updated without changing the client
  - Multiple tools servers can be composed (add their tools together)
  - Any MCP server (even third-party) works out of the box

Flow:
  MCPBridge.run(goal)
    -> connect to MCP server (subprocess)
    -> fetch tool definitions (dynamic discovery)
    -> convert to Anthropic format
    -> run Claude agentic loop
       -> Claude requests tool_use
       -> MCPBridge calls tool on MCP server (not local handler)
       -> returns result to Claude
    -> disconnect
    -> return BridgeResult
"""

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from config.settings import DEFAULT_MODEL, MAX_TOKENS
from core.client import ClaudeClient
from core.cost_tracker import CostTracker
from mcp.client import MCPClient

console = Console(legacy_windows=False)

# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class BridgeResult:
    """
    Structured result from MCPBridge.run().

    CCA-F — Agents:
      Consistent with AgentResult / OrchestratorResult so the bridge
      fits into the same programmatic patterns as Phase 3 agents.
    """
    success: bool
    output: str
    tools_used: list[str]     # names of MCP tools Claude called
    total_cost: float
    error: str = ""


# ── Bridge ─────────────────────────────────────────────────────────────────────

class MCPBridge:
    """
    Bridges an MCP server's tools to the Claude API.

    CCA-F — MCP:
      The bridge is the consumer side of MCP. It:
        1. Connects to an MCP server
        2. Discovers tools dynamically (tools/list)
        3. Converts to Anthropic format
        4. Runs Claude with those tools
        5. Routes Claude's tool_use requests back to the MCP server

    Unlike Phase 2's ToolExecutor (which calls local Python handlers),
    MCPBridge makes network/IPC calls to the MCP server for each tool.
    """

    def __init__(self, server_script: str | None = None, model: str = DEFAULT_MODEL) -> None:
        from config.settings import MCP_SERVER_SCRIPT
        self._server_script = str(
            Path(server_script or MCP_SERVER_SCRIPT).resolve()
        )
        self._model = model

    def run(self, goal: str) -> BridgeResult:
        """
        Synchronous entry point — runs the async bridge in an event loop.

        CCA-F: Click commands are synchronous; we bridge to async here
        using asyncio.run() so the caller doesn't need to know about
        Python's async model.
        """
        return asyncio.run(self._run_async(goal))

    # ── Async implementation ───────────────────────────────────────────────────

    async def _run_async(self, goal: str) -> BridgeResult:
        """Inner async implementation used by run()."""
        client = ClaudeClient(model=self._model)
        tracker = CostTracker()
        tools_used: list[str] = []

        try:
            async with MCPClient(self._server_script) as mcp:
                # ── Step 1: Dynamic tool discovery ────────────────────────────
                # CCA-F: This is the key MCP advantage. We ask the server
                # what tools it provides rather than hardcoding them.
                mcp_tools = await mcp.list_tools()
                anthropic_tools = mcp.to_anthropic_tools(mcp_tools)

                tool_names = [t.name for t in mcp_tools]
                console.print(
                    f"[dim cyan][MCP] Connected to adoptviaai-server[/dim cyan]"
                )
                console.print(
                    f"[dim cyan][MCP Tools] {', '.join(tool_names)}[/dim cyan]"
                )

                # ── Step 2: Run Claude agentic loop ───────────────────────────
                messages = [{"role": "user", "content": goal}]
                final_text = await self._agentic_loop(
                    client, tracker, mcp, anthropic_tools, messages, tools_used
                )

            return BridgeResult(
                success=True,
                output=final_text,
                tools_used=tools_used,
                total_cost=tracker.session_total(),
            )

        except Exception as exc:
            return BridgeResult(
                success=False,
                output="",
                tools_used=tools_used,
                total_cost=tracker.session_total() if tracker else 0.0,
                error=str(exc),
            )

    async def _agentic_loop(
        self,
        client: ClaudeClient,
        tracker: CostTracker,
        mcp: MCPClient,
        anthropic_tools: list[dict],
        messages: list[dict],
        tools_used: list[str],
        max_iterations: int = 10,
    ) -> str:
        """
        Claude agentic loop that routes tool calls to the MCP server.

        CCA-F — MCP vs Phase 2:
          Phase 2: tool handlers are Python callables in the tool registry.
          Phase 4: tool handlers are MCP tool calls (IPC to server process).
          The loop logic is identical — only the execution layer differs.
        """
        for _ in range(max_iterations):
            # Each iteration is one Claude API call
            response = client.send_message(
                messages=messages,
                system_prompt=(
                    "You are a helpful assistant with access to MCP tools. "
                    "Use the available tools to answer the user's question. "
                    "Be concise and direct."
                ),
                max_tokens=MAX_TOKENS,
                tools=anthropic_tools if anthropic_tools else None,
            )
            tracker.add_call(response.usage, self._model)

            if response.stop_reason == "end_turn":
                return _extract_text(response.content)

            elif response.stop_reason == "tool_use":
                # CCA-F: Append assistant message with tool_use blocks
                assistant_content = _blocks_to_dicts(response.content)
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute each tool call via MCP
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input
                    tools_used.append(tool_name)

                    console.print(
                        f"  [cyan]>>[/cyan] [bold]MCP Tool:[/bold] "
                        f"[yellow]{tool_name}[/yellow]  "
                        f"[dim]{_preview(tool_input)}[/dim]",
                        end="  ",
                    )

                    try:
                        # CCA-F: Route to MCP server (not local Python handler)
                        result = await mcp.call_tool(tool_name, tool_input)
                        result_str = (
                            json.dumps(result, ensure_ascii=False)
                            if not isinstance(result, str)
                            else result
                        )
                        console.print("[green]OK[/green]")
                    except Exception as exc:
                        result_str = json.dumps({"error": str(exc)})
                        console.print(f"[red]FAIL[/red] [dim]{exc}[/dim]")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason — return whatever text we have
                return _extract_text(response.content)

        return "[Warning: MCP agentic loop reached max_iterations]"


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_text(content_blocks) -> str:
    """Concatenate text blocks from a Claude response."""
    return "".join(
        getattr(b, "text", "") for b in content_blocks if b.type == "text"
    )


def _blocks_to_dicts(content_blocks) -> list[dict]:
    """Convert SDK content block objects to plain dicts."""
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result


def _preview(obj: dict | None, max_len: int = 60) -> str:
    """Short JSON preview of a dict for display."""
    if not obj:
        return "{}"
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= max_len else s[:max_len - 3] + "..."
