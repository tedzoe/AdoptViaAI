"""
tools/registry.py — Tool registry

CCA-F Domain 4: Tool Use
The registry is the bridge between:
  - Tool definitions (JSON Schema sent to the Claude API)
  - Handler functions (Python callables invoked locally)

Claude sees only the definitions and decides which tools to call.
The registry dispatches those calls to the matching handler.
"""

from typing import Callable


class ToolRegistry:
    """
    Holds tool definitions (sent to Claude API) and handler functions
    (executed locally when Claude requests a tool call).

    CCA-F Domain 4 — Tool Use:
      Tool definitions follow the JSON Schema format required by the
      Anthropic Messages API. Claude uses the description and
      input_schema to decide when and how to invoke each tool.
      Handlers are never sent to the API — they run locally.
    """

    def __init__(self) -> None:
        self._definitions: list[dict] = []
        self._handlers: dict[str, Callable] = {}

    def register(self, tool_definition: dict, handler_function: Callable) -> None:
        """
        Register a tool.

        Args:
            tool_definition:    JSON-Schema dict with keys:
                                  name, description, input_schema
            handler_function:   Callable(inputs: dict) → dict
        """
        name = tool_definition.get("name")
        if not name:
            raise ValueError("tool_definition must have a 'name' field")

        self._definitions.append(tool_definition)
        self._handlers[name] = handler_function

    def get_definitions(self) -> list[dict]:
        """
        Return the list of tool schemas to pass to messages.create().

        CCA-F Domain 4:
          This list goes directly into the `tools` parameter of the
          Anthropic Messages API call. Claude reads it to understand
          what tools are available and what inputs they expect.
        """
        return list(self._definitions)

    def get_handler(self, tool_name: str) -> Callable:
        """Return the handler callable for a tool. Raises KeyError if unknown."""
        if tool_name not in self._handlers:
            raise KeyError(
                f"Unknown tool '{tool_name}'. "
                f"Available: {list(self._handlers.keys())}"
            )
        return self._handlers[tool_name]

    def list_tools(self) -> list[dict]:
        """Return a summary list for display (name, description, parameter names)."""
        result = []
        for defn in self._definitions:
            props = defn.get("input_schema", {}).get("properties", {})
            required = defn.get("input_schema", {}).get("required", [])
            params = []
            for param_name, param_schema in props.items():
                marker = "*" if param_name in required else ""
                params.append(f"{marker}{param_name}")
            result.append(
                {
                    "name": defn["name"],
                    "description": defn.get("description", ""),
                    "parameters": params,
                }
            )
        return result

    def tool_names(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._handlers.keys())

    def __len__(self) -> int:
        return len(self._definitions)
