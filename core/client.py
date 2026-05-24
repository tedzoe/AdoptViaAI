"""
core/client.py — Anthropic API wrapper

CCA-F Domains:
  - API Fundamentals: messages.create, response structure, error handling
  - Prompt Caching (Domain 5): cache_control breakpoints on system prompts
    reduce repeated-call costs by up to 90% on cache hits.
  - Tool Use (Domain 4): optional tools parameter passed to messages.create
  - Model Selection: shortname → full model ID mapping with clear cost tiers

Phase 2 change: send_message() now accepts an optional `tools` parameter.
All Phase 1 callers that omit `tools` behave identically to before.
"""

import anthropic

from config.settings import ANTHROPIC_API_KEY, MAX_TOKENS

# ── Model registry ─────────────────────────────────────────────────────────────
# CCA-F Note: Full model IDs are pinned so the code is reproducible and
# behaviour does not change when Anthropic updates the "latest" alias.
MODEL_MAP: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",    # Fast, cheapest — default for dev
    "sonnet": "claude-sonnet-4-20250514",    # Balanced intelligence / cost
    "opus": "claude-opus-4-20250514",        # Highest capability, highest cost
}

DEFAULT_SHORTNAME = "haiku"


class ClaudeClient:
    """
    Thin wrapper around anthropic.Anthropic that:
      - Resolves model shortnames to full IDs
      - Injects prompt-caching cache_control on every system prompt
      - Accepts optional tool definitions for Phase 2 tool use
      - Normalises error messages so the CLI can display them cleanly
    """

    def __init__(self, model: str = DEFAULT_SHORTNAME) -> None:
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = model  # accepts shortname or full ID

    # ── Public API ─────────────────────────────────────────────────────────────

    def switch_model(self, model: str) -> None:
        """Switch the active model (shortname or full ID)."""
        self.model = model

    @property
    def model_id(self) -> str:
        """Resolve shortname → full model ID for API calls."""
        return MODEL_MAP.get(self.model, self.model)

    def send_message(
        self,
        messages: list[dict],
        system_prompt: str,
        max_tokens: int = MAX_TOKENS,
        tools: list[dict] | None = None,
    ) -> anthropic.types.Message:
        """
        Send a messages.create request with prompt caching on the system prompt.

        CCA-F — Prompt Caching:
          The system prompt is wrapped in a list with cache_control type "ephemeral".
          On the first call the prompt is written to Anthropic's edge cache
          (billed as cache_creation_input_tokens at ~1.25× the normal rate).
          Every subsequent call within the 5-minute TTL reads from cache
          (billed as cache_read_input_tokens at ~0.1× the normal rate) —
          a 10× cost saving on the stable prefix.

        CCA-F — Tool Use (Domain 4):
          When `tools` is provided, Claude can respond with tool_use content blocks
          instead of (or in addition to) text. The caller (ToolExecutor) detects
          stop_reason == "tool_use" and runs the agentic loop.

        Returns the full Message object so callers can inspect usage stats
        and stop_reason.
        """
        system = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},  # ← prompt caching
            }
        ]

        kwargs: dict = {
            "model": self.model_id,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }

        # CCA-F Domain 4: Include tool definitions only when tools are active.
        # Passing an empty list is treated differently from omitting the field.
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise RuntimeError(
                "Authentication failed — check your ANTHROPIC_API_KEY in .env"
            )
        except anthropic.RateLimitError:
            raise RuntimeError(
                "Rate limit exceeded — wait a moment and try again"
            )
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"Anthropic API error {exc.status_code}: {exc.message}")

        return response

    def summarize(self, transcript: str) -> str:
        """
        Summarise a conversation transcript using haiku (cheapest model).

        CCA-F — Context Management:
          Always use haiku for summarisation to keep costs minimal regardless
          of what model the user has selected for their main conversation.
        """
        try:
            response = self._client.messages.create(
                model=MODEL_MAP["haiku"],    # always haiku — cost-optimised
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Summarise the following conversation concisely, "
                            "preserving all key facts and decisions:\n\n"
                            + transcript
                        ),
                    }
                ],
            )
        except Exception as exc:
            raise RuntimeError(f"Summarisation failed: {exc}") from exc

        return response.content[0].text
