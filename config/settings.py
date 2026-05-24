"""
config/settings.py — Application settings loaded from .env

CCA-F Domain: API Fundamentals
Demonstrates: environment-based configuration, API key management,
dotenv loading pattern used in all professional Claude integrations.

CCA-F Note — Windows UTF-8 compatibility:
  Claude's responses frequently contain Unicode characters (—, →, ×, etc.).
  On Windows, Python defaults to cp1252 which cannot encode these.
  We reconfigure stdout/stderr to UTF-8 here (the earliest import point)
  so all downstream Rich console output handles the full Unicode range.
  Pair with Console(legacy_windows=False) so Rich writes to sys.stdout
  instead of the Win32 console API (which ignores the stdout encoding).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── Windows UTF-8 compatibility ────────────────────────────────────────────────
# Must be done BEFORE any Console() is constructed.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

# Load .env from the project root (two levels up: config/ → project root)
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ── Ensure logs/ directory exists ─────────────────────────────────────────────
_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# ── API credentials ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")

# ── Model selection ────────────────────────────────────────────────────────────
# CCA-F Note: Default to haiku for cost savings during development.
# Escalate to sonnet/opus only when the task demands it.
DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "haiku")

# ── Token limits ───────────────────────────────────────────────────────────────
MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "2048"))

# ── Cost alerting ──────────────────────────────────────────────────────────────
COST_WARNING_THRESHOLD: float = float(os.getenv("COST_WARNING_THRESHOLD", "0.10"))

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE: str = str(_LOG_DIR / "usage.log")

# ── Default system prompt ──────────────────────────────────────────────────────
SYSTEM_PROMPT_DEFAULT: str = os.getenv("SYSTEM_PROMPT_DEFAULT", "default")

# ── MCP settings (Phase 4) ─────────────────────────────────────────────────────
# CCA-F Domain: MCP — server script path is relative to project root.
# Override with env vars if needed for non-standard layouts.
MCP_SERVER_SCRIPT: str = os.getenv(
    "MCP_SERVER_SCRIPT",
    str(Path(__file__).parent.parent / "mcp" / "server.py"),
)
MCP_SERVER_NAME: str = os.getenv("MCP_SERVER_NAME", "adoptviaai-server")
MCP_TIMEOUT: int = int(os.getenv("MCP_TIMEOUT", "30"))


def validate_api_key() -> None:
    """
    Call at the start of any command that hits the API.
    Prints a clear, actionable error and exits if the key is missing.

    CCA-F Domain: API Fundamentals — never let an application silently
    fail due to a missing credential; surface errors early and clearly.
    """
    if not ANTHROPIC_API_KEY:
        # Import Rich here to avoid a circular import at module level
        from rich.console import Console

        Console(legacy_windows=False).print(
            "[red bold]Error:[/red bold] ANTHROPIC_API_KEY is not set.\n\n"
            "Fix:\n"
            "  1. Copy [cyan].env.example[/cyan] to [cyan].env[/cyan]\n"
            "  2. Open [cyan].env[/cyan] and set [cyan]ANTHROPIC_API_KEY=sk-ant-...[/cyan]\n"
        )
        sys.exit(1)
