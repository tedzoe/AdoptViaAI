"""
tools/builtin.py — Built-in tool implementations for AdoptviaAI

CCA-F Domain 4: Tool Use — Function Calling
Each tool has two parts:
  1. A JSON Schema definition (sent to Claude API so Claude knows how to call it)
  2. A handler function (executed locally when Claude requests the tool)

Security principles applied here:
  - calculator: AST-based safe evaluation — NEVER use eval() directly
  - file_reader: read-only, no write access
  - save_note: restricted to notes/ directory, filename sanitised to prevent
               path traversal attacks
  - get_project_info: read-only introspection, no side effects
"""

import ast
import operator as op
from pathlib import Path

# Notes directory — always resolved relative to project root
_NOTES_DIR = Path(__file__).parent.parent / "notes"

# ── Safe expression evaluator ──────────────────────────────────────────────────

# CCA-F Security Note:
#   eval() executes arbitrary Python code — never use it to evaluate
#   user-supplied expressions. The AST approach parses the expression
#   into a syntax tree and only allows safe numeric operations.
_ALLOWED_OPS: dict = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _safe_eval_node(node: ast.expr) -> float:
    """Recursively evaluate a numeric AST node. Raises ValueError on unsafe input."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Non-numeric constant: {node.value!r}")

    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Operator '{op_type.__name__}' is not allowed")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        # Guard against enormous exponents
        if op_type == ast.Pow and (abs(right) > 300 or abs(left) > 1e15):
            raise ValueError("Exponent or base too large")
        return _ALLOWED_OPS[op_type](left, right)

    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPS:
            raise ValueError(f"Unary operator '{op_type.__name__}' is not allowed")
        return _ALLOWED_OPS[op_type](_safe_eval_node(node.operand))

    else:
        raise ValueError(
            f"Expression contains unsupported element: {type(node).__name__}"
        )


def _safe_calculate(expression: str) -> float:
    """Parse and safely evaluate a math expression string."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression: {exc}") from exc
    return _safe_eval_node(tree.body)


# ── Tool 1: calculator ─────────────────────────────────────────────────────────

CALCULATOR_SCHEMA = {
    "name": "calculator",
    "description": (
        "Safely evaluate a mathematical expression and return the numeric result. "
        "Supports: +, -, *, /, //, %, ** (exponentiation). "
        "Example inputs: '847 * 23', '(10 + 5) / 3', '2 ** 10'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate, e.g. '847 * 23'",
            }
        },
        "required": ["expression"],
    },
}


def calculator_handler(inputs: dict) -> dict:
    """
    Handler for the calculator tool.

    CCA-F Domain 4 + Security:
      Uses AST-based safe evaluation instead of eval() to prevent
      code injection. Only numeric literals and arithmetic operators
      are permitted.
    """
    expression = inputs.get("expression", "").strip()
    if not expression:
        return {"error": "expression is required", "expression": ""}
    try:
        result = _safe_calculate(expression)
        # Return int if result is a whole number for cleaner output
        display_result = int(result) if result == int(result) else result
        return {"result": display_result, "expression": expression}
    except (ValueError, ZeroDivisionError) as exc:
        return {"error": str(exc), "expression": expression}


# ── Tool 2: file_reader ────────────────────────────────────────────────────────

FILE_READER_SCHEMA = {
    "name": "file_reader",
    "description": (
        "Read the contents of a local file and return its text. "
        "Useful for reading source code, config files, or documents. "
        "Only reads files — never writes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the file to read (relative to the current directory)",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to return (default: 100)",
            },
        },
        "required": ["filepath"],
    },
}


def file_reader_handler(inputs: dict) -> dict:
    """
    Handler for the file_reader tool.

    CCA-F Domain 4:
      Read-only tool — no write operations. Graceful error handling
      for missing files rather than crashing the agentic loop.
    """
    filepath = inputs.get("filepath", "")
    max_lines = int(inputs.get("max_lines", 100))

    try:
        path = Path(filepath)
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        truncated = lines[:max_lines]
        content = "".join(truncated)
        return {
            "content": content,
            "lines_read": len(truncated),
            "total_lines": len(lines),
            "truncated": len(lines) > max_lines,
            "filepath": str(path),
        }
    except FileNotFoundError:
        return {
            "error": f"File not found: {filepath}",
            "filepath": filepath,
        }
    except PermissionError:
        return {
            "error": f"Permission denied: {filepath}",
            "filepath": filepath,
        }
    except Exception as exc:
        return {"error": str(exc), "filepath": filepath}


# ── Tool 3: save_note ──────────────────────────────────────────────────────────

SAVE_NOTE_SCHEMA = {
    "name": "save_note",
    "description": (
        "Save text content to a note file in the notes/ directory. "
        "Can create a new file or append to an existing one. "
        "Files are always saved inside the notes/ directory — "
        "subdirectory paths are not permitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Filename for the note (e.g. 'math_results.txt'). "
                               "No path separators allowed.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the note file.",
            },
            "append": {
                "type": "boolean",
                "description": "If true, append to existing file. "
                               "If false (default), overwrite.",
            },
        },
        "required": ["filename", "content"],
    },
}


def save_note_handler(inputs: dict) -> dict:
    """
    Handler for the save_note tool.

    CCA-F Domain 4 + Security:
      Restricts writes to the notes/ directory. Strips path components
      from the filename (Path(name).name) and verifies the resolved
      path is inside notes/ to prevent directory traversal attacks.
    """
    filename = inputs.get("filename", "").strip()
    content = inputs.get("content", "")
    append = bool(inputs.get("append", False))

    if not filename:
        return {"saved": False, "error": "filename is required"}

    # Security: strip any directory components — only bare filename allowed
    safe_name = Path(filename).name
    if safe_name != filename and "/" in filename or "\\" in filename:
        return {
            "saved": False,
            "error": "Path separators not allowed in filename",
        }
    if not safe_name:
        return {"saved": False, "error": "Invalid filename"}

    try:
        _NOTES_DIR.mkdir(parents=True, exist_ok=True)
        filepath = (_NOTES_DIR / safe_name).resolve()

        # Double-check resolved path is inside notes/ (defence in depth)
        if not str(filepath).startswith(str(_NOTES_DIR.resolve())):
            return {"saved": False, "error": "Path traversal detected — write blocked"}

        mode = "a" if append else "w"
        with filepath.open(mode, encoding="utf-8") as f:
            f.write(content)

        return {
            "saved": True,
            "filepath": str(filepath),
            "bytes_written": len(content.encode("utf-8")),
            "mode": "appended" if append else "overwritten",
        }
    except Exception as exc:
        return {"saved": False, "error": str(exc)}


# ── Tool 4: get_project_info ───────────────────────────────────────────────────

GET_PROJECT_INFO_SCHEMA = {
    "name": "get_project_info",
    "description": (
        "Return metadata about the AdoptviaAI application — version, phase, "
        "available commands, models, and tools. Useful for introspection "
        "or when the user asks 'what can this tool do?'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def get_project_info_handler(inputs: dict) -> dict:
    """
    Handler for the get_project_info tool.

    CCA-F Domain 4:
      Demonstrates a zero-input tool. Shows Claude how to call a tool
      when no user data is required — Claude infers the call purely from
      context (e.g. the user asks 'what version is this?').
    """
    return {
        "name": "AdoptviaAI",
        "tagline": "AI adoption done right",
        "version": "0.5.0",
        "phase": "Phase 5 -- Safety & Guardrails",
        "commands_available": [
            "chat", "chat --safe", "chat --tools",
            "ask", "ask --safe", "ask --tools",
            "summary", "models", "prompts",
            "tools list", "tools run",
            "agent list", "agent run",
            "chain list", "chain run",
            "mcp serve", "mcp tools", "mcp run", "mcp status",
            "safety status", "safety check", "safety audit",
            "version",
        ],
        "models_available": {
            "haiku":  "claude-haiku-4-5-20251001 (default, cheapest)",
            "sonnet": "claude-sonnet-4-20250514",
            "opus":   "claude-opus-4-20250514",
        },
        "tools_available": [
            "calculator",
            "file_reader",
            "save_note",
            "get_project_info",
        ],
        "cca_f_domains_demonstrated": [
            "Domain 1: API Fundamentals",
            "Domain 2: Prompt Engineering",
            "Domain 3: Context Management",
            "Domain 4: Tool Use & Function Calling",
            "Domain 5: Agents & Orchestration",
            "Domain 6: MCP (Model Context Protocol)",
            "Domain 7: Safety & Responsible Use",
        ],
    }


# ── Registry helper ────────────────────────────────────────────────────────────

# List of (schema, handler) pairs — import and register them all at once
BUILTIN_TOOLS: list[tuple[dict, object]] = [
    (CALCULATOR_SCHEMA, calculator_handler),
    (FILE_READER_SCHEMA, file_reader_handler),
    (SAVE_NOTE_SCHEMA, save_note_handler),
    (GET_PROJECT_INFO_SCHEMA, get_project_info_handler),
]
