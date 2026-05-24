"""
mcp/ — Phase 4: Model Context Protocol

CCA-F Domain: MCP — Model Context Protocol
This package demonstrates the MCP pattern:
  - server.py : FastMCP server exposing notes/ tools and resources
  - client.py : async JSON-RPC client that drives the server as a subprocess
  - bridge.py : synchronous bridge that connects MCP tools to the Claude API

Why MCP vs hardcoded tools (Phase 2)?
  Phase 2: tool schemas are written in Python and registered at startup.
  Phase 4: tool schemas are DISCOVERED at runtime by asking the server.
  MCP decouples tool providers from tool consumers — any MCP-compatible
  client (Claude Desktop, avai, your own app) can use the same server.

Import pattern from main.py:
  from mcp.client import MCPClient      # async JSON-RPC client
  from mcp.bridge import MCPBridge      # sync wrapper for the agentic loop
"""
