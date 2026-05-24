"""
mcp/client.py — Async MCP client using JSON-RPC over stdio

CCA-F Domain: MCP — Model Context Protocol
Demonstrates: MCP protocol at the transport level.

Why not use the pip mcp client library here?
  This file is imported by main.py, which has the project root in sys.path.
  The local mcp/ package then shadows the pip mcp package, making direct
  imports of mcp.client.session impossible. We implement the MCP protocol
  manually over JSON-RPC 2.0 — which is MORE educational for CCA-F because
  it makes the protocol visible rather than hiding it behind an SDK.

MCP Protocol (JSON-RPC 2.0 over stdio):
  Each message is a JSON object on a single line (\n terminated).
  Request:      {"jsonrpc":"2.0","method":"...","params":{...},"id":N}
  Notification: {"jsonrpc":"2.0","method":"...","params":{...}}
  Response:     {"jsonrpc":"2.0","result":{...},"id":N}

Usage:
    import asyncio
    from mcp.client import MCPClient

    async def main():
        async with MCPClient("mcp/server.py") as client:
            tools = await client.list_tools()
            result = await client.call_tool("notes_list", {})

    asyncio.run(main())
"""

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Protocol version ──────────────────────────────────────────────────────────
_PROTOCOL_VERSION = "2025-11-25"
_CLIENT_INFO = {"name": "avai-mcp-client", "version": "0.4.0"}


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class MCPTool:
    """
    MCP tool definition as returned by tools/list.

    CCA-F: MCP tools have an inputSchema (JSON Schema for parameters).
    The bridge converts inputSchema -> input_schema for the Anthropic API.
    """
    name: str
    description: str
    input_schema: dict    # already renamed from inputSchema for Anthropic compat


@dataclass
class MCPResource:
    """MCP resource definition as returned by resources/list."""
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"


# ── Client ─────────────────────────────────────────────────────────────────────

class MCPClient:
    """
    Async MCP client that drives an MCP server as a subprocess.

    CCA-F — MCP Transport:
      Uses stdio transport — the server reads JSON-RPC from its stdin and
      writes responses to its stdout. We manage the subprocess lifecycle
      and the JSON-RPC message framing.

    Usage as async context manager:
        async with MCPClient("mcp/server.py") as client:
            tools = await client.list_tools()
    """

    def __init__(self, server_script: str) -> None:
        """
        Args:
            server_script: Path to the server.py script to run as subprocess.
        """
        self._server_script = str(Path(server_script).resolve())
        self._proc: asyncio.subprocess.Process | None = None
        self._msg_id: int = 0
        self._initialized: bool = False

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    # ── Connection lifecycle ───────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Spawn the server subprocess and perform MCP handshake.

        CCA-F — MCP Handshake:
          1. Send initialize request with protocol version + client info
          2. Receive InitializeResult (server capabilities)
          3. Send notifications/initialized (acknowledge the server)
          After this, tools/list and tools/call are available.
        """
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            self._server_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,   # suppress server startup messages
        )

        # MCP handshake — Step 1: initialize
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": _CLIENT_INFO,
            },
        )
        if not result:
            raise ConnectionError("MCP initialize failed — no response from server")

        # Step 2: notify the server we're done initialising
        await self._notify("notifications/initialized", {})
        self._initialized = True

    async def disconnect(self) -> None:
        """Terminate the server subprocess cleanly."""
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        self._proc = None
        self._initialized = False

    # ── MCP API ────────────────────────────────────────────────────────────────

    async def list_tools(self) -> list[MCPTool]:
        """
        Retrieve all tools exposed by the server.

        CCA-F — Dynamic Tool Discovery:
          This is the key MCP advantage over Phase 2: we ask the server
          what tools exist at runtime rather than hardcoding them.
          The same avai client works with any MCP server.
        """
        result = await self._request("tools/list", {})
        tools = []
        for t in (result or {}).get("tools", []):
            # Rename inputSchema -> input_schema for Anthropic API compat
            raw_schema = t.get("inputSchema", {})
            tools.append(MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=raw_schema,
            ))
        return tools

    async def list_resources(self) -> list[MCPResource]:
        """Retrieve all resources exposed by the server."""
        result = await self._request("resources/list", {})
        resources = []
        for r in (result or {}).get("resources", []):
            resources.append(MCPResource(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", "text/plain"),
            ))
        return resources

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """
        Invoke a tool by name and return its result.

        CCA-F — Tool Invocation:
          The result is typically a list of content blocks.
          We extract the text content and return it as a plain value.
        """
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        # Extract text from content blocks
        content_blocks = (result or {}).get("content", [])
        if not content_blocks:
            return result or {}
        # Concatenate text blocks
        parts = [
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        ]
        text = "".join(parts)
        # Try to parse as JSON for structured results
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    async def read_resource(self, uri: str) -> str:
        """Read a resource by its URI and return its content as text."""
        result = await self._request("resources/read", {"uri": uri})
        contents = (result or {}).get("contents", [])
        if not contents:
            return ""
        return contents[0].get("text", "")

    def to_anthropic_tools(self, tools: list[MCPTool]) -> list[dict]:
        """
        Convert MCPTool definitions to Anthropic API tool format.

        CCA-F — MCP <> Claude API Bridge:
          MCP uses 'inputSchema' (camelCase).
          Anthropic API uses 'input_schema' (snake_case).
          MCPTool already stores it as input_schema (renamed in list_tools).
          This method produces the exact dict format Claude's API expects.
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    # ── JSON-RPC transport (private) ───────────────────────────────────────────

    async def _request(self, method: str, params: dict) -> dict | None:
        """
        Send a JSON-RPC request and wait for the response.

        CCA-F — MCP Protocol:
          Requests have an 'id' field; the server echoes the same id
          in its response so we can correlate them.
        """
        if not self._proc or not self._proc.stdin:
            raise ConnectionError("MCP server not connected")

        self._msg_id += 1
        msg_id = self._msg_id

        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": msg_id,
        }
        await self._send(request)

        # Wait for the matching response
        for _ in range(50):   # up to 50 lines before giving up
            line = await self._readline()
            if line is None:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip notifications (no 'id') and mismatched responses
            if msg.get("id") != msg_id:
                continue

            if "error" in msg:
                raise RuntimeError(
                    f"MCP error on '{method}': {msg['error'].get('message', msg['error'])}"
                )
            return msg.get("result")

        return None

    async def _notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if not self._proc or not self._proc.stdin:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._send(notification)

    async def _send(self, obj: dict) -> None:
        """Serialise obj to JSON and write a newline-terminated line to stdin."""
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _readline(self) -> str | None:
        """Read one newline-terminated line from the server's stdout."""
        if not self._proc or not self._proc.stdout:
            return None
        try:
            data = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=10.0
            )
            if data:
                return data.decode("utf-8").strip()
        except asyncio.TimeoutError:
            pass
        return None
