"""
mcp/server.py — AdoptviaAI MCP Server

CCA-F Domain: MCP — Model Context Protocol
Demonstrates: MCP server implementation using FastMCP.
This server exposes the notes/ directory as both TOOLS and RESOURCES.

Key concepts:
  TOOLS      — callable actions (CRUD operations on notes)
  RESOURCES  — readable data sources (notes as addressable URIs)

stdio transport: The server communicates over stdin/stdout using
JSON-RPC 2.0. Claude Desktop, avai's MCPClient, or any MCP-compatible
client can connect by spawning this script as a subprocess.

IMPORTANT — import path:
  This file is run AS A SUBPROCESS (not imported). When run as
  'python mcp/server.py', Python sets sys.path[0] = the script's
  directory (mcp/). There is no nested mcp/ inside that directory,
  so 'from mcp.server.fastmcp import FastMCP' correctly resolves
  to the pip mcp package in site-packages — no naming conflict.
"""

import sys
from pathlib import Path

# Resolve notes/ relative to this server file (two dirs up from mcp/)
_NOTES_DIR = Path(__file__).parent.parent / "notes"

# ── Import FastMCP from pip package ───────────────────────────────────────────
# When this script runs as a subprocess, sys.path[0] = <project>/mcp/
# There is no mcp/ subdirectory there, so Python finds the pip package.
# No sys.path manipulation needed.

from mcp.server.fastmcp import FastMCP  # noqa: E402  (pip package, not local)

# ── Server definition ──────────────────────────────────────────────────────────
# CCA-F: Name the server — clients use this to identify the tool provider.

mcp_app = FastMCP("adoptviaai-server")


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — callable actions
# CCA-F: Tools are invoked by Claude (or any MCP client) to perform actions.
#        Unlike resources, tools can have side effects (delete, write, etc.)
# ══════════════════════════════════════════════════════════════════════════════

@mcp_app.tool()
def notes_list() -> dict:
    """List all saved notes in the notes/ directory."""
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        f.name for f in sorted(_NOTES_DIR.iterdir())
        if f.is_file() and f.name != ".gitkeep"
    ]
    return {"notes": files, "count": len(files)}


@mcp_app.tool()
def notes_read(filename: str) -> dict:
    """Read a specific note by filename."""
    safe_name = Path(filename).name  # strip any path components
    filepath = _NOTES_DIR / safe_name
    try:
        content = filepath.read_text(encoding="utf-8")
        return {"content": content, "filename": safe_name, "bytes": len(content)}
    except FileNotFoundError:
        return {"error": f"Note not found: {safe_name}", "filename": safe_name}
    except Exception as exc:
        return {"error": str(exc), "filename": safe_name}


@mcp_app.tool()
def notes_write(filename: str, content: str, append: bool = False) -> dict:
    """
    Create or overwrite a note. Set append=True to add to an existing note.

    CCA-F Domain: MCP — Tool with side effects.
    Security: path traversal is rejected; writes are restricted to notes/.
    """
    # Reject path traversal attempts
    if ".." in filename or "/" in filename or "\\" in filename:
        return {
            "written": False,
            "filepath": "",
            "bytes_written": 0,
            "error": "Path traversal rejected — use bare filename only",
        }

    safe_name = Path(filename).name
    if not safe_name:
        return {"written": False, "filepath": "", "bytes_written": 0,
                "error": "Invalid filename"}

    try:
        _NOTES_DIR.mkdir(parents=True, exist_ok=True)
        filepath = (_NOTES_DIR / safe_name).resolve()

        # Defence in depth: verify resolved path stays inside notes/
        if not str(filepath).startswith(str(_NOTES_DIR.resolve())):
            return {
                "written": False,
                "filepath": "",
                "bytes_written": 0,
                "error": "Path traversal blocked",
            }

        mode = "a" if append else "w"
        with open(filepath, mode, encoding="utf-8") as fh:
            fh.write(content)

        return {
            "written": True,
            "filepath": str(filepath),
            "bytes_written": len(content.encode("utf-8")),
        }
    except Exception as exc:
        return {"written": False, "filepath": "", "bytes_written": 0,
                "error": str(exc)}


@mcp_app.tool()
def notes_delete(filename: str) -> dict:
    """Delete a note by filename."""
    safe_name = Path(filename).name
    filepath = _NOTES_DIR / safe_name
    try:
        filepath.unlink()
        return {"deleted": True, "filename": safe_name}
    except FileNotFoundError:
        return {"deleted": False, "filename": safe_name, "error": "File not found"}
    except Exception as exc:
        return {"deleted": False, "filename": safe_name, "error": str(exc)}


@mcp_app.tool()
def project_status() -> dict:
    """Get AdoptviaAI project status — current phase, version, and counts."""
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    notes_count = len([
        f for f in _NOTES_DIR.iterdir()
        if f.is_file() and f.name != ".gitkeep"
    ])
    return {
        "name": "AdoptviaAI",
        "version": "0.5.0",
        "phase": "Phase 5 -- Safety & Guardrails",
        "tools_count": 4,       # calculator, file_reader, save_note, get_project_info
        "agents_count": 3,      # researcher, writer, orchestrator
        "chains_count": 3,      # summarize-and-save, analyze-and-recommend, validate
        "mcp_tools_count": 5,   # notes_list, notes_read, notes_write, notes_delete, project_status
        "notes_count": notes_count,
        "notes_dir": str(_NOTES_DIR),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES — addressable data (read-only, URI-based)
# CCA-F: Resources are like GET endpoints — no side effects, just data.
#        Clients can subscribe to resource changes or read on demand.
# ══════════════════════════════════════════════════════════════════════════════

@mcp_app.resource("notes://list")
def notes_list_resource() -> str:
    """
    Directory listing of all notes as plain text.

    CCA-F: Resources use URI addressing (notes://list) rather than function
    names. This makes them easy to reference in prompts and subscriptions.
    """
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        f.name for f in sorted(_NOTES_DIR.iterdir())
        if f.is_file() and f.name != ".gitkeep"
    ]
    if not files:
        return "No notes found."
    return "\n".join(f"  - {name}" for name in files)


@mcp_app.resource("notes://{filename}")
def notes_read_resource(filename: str) -> str:
    """
    Content of a specific note, addressed by filename.

    URI pattern: notes://math_test.txt  ->  returns content of math_test.txt

    CCA-F: URI templates (with {parameters}) let clients address individual
    resources dynamically without enumerating them upfront.
    """
    safe_name = Path(filename).name
    filepath = _NOTES_DIR / safe_name
    try:
        return filepath.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[Error: Note '{safe_name}' not found in notes/]"
    except Exception as exc:
        return f"[Error reading '{safe_name}': {exc}]"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # CCA-F: stdio is the standard transport for local MCP servers.
    # Claude Desktop and avai's MCPClient both use stdio transport.
    # The server blocks here, reading JSON-RPC from stdin, writing to stdout.
    print(
        "adoptviaai-server starting (stdio transport)\n"
        "Tools: notes_list, notes_read, notes_write, notes_delete, project_status\n"
        "Resources: notes://list, notes://{filename}",
        file=sys.stderr,
        flush=True,
    )
    mcp_app.run(transport="stdio")
