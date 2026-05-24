"""
core/cost_tracker.py — Token usage and cost tracking

CCA-F Domain: Cost Management
Demonstrates:
  - Parsing all four token types from response.usage
  - Per-model pricing applied correctly to each token type
  - Session-level accumulation for budget awareness
  - CSV logging for API calls + plain-text logging for tool calls
  - Rich-formatted summaries for UX polish

Phase 2 addition: log_tool_call() method for recording tool invocations.
"""

import csv
import os
from datetime import datetime

from rich.console import Console
from rich.table import Table

from config.settings import LOG_FILE

console = Console(legacy_windows=False)

# ── Pricing table ──────────────────────────────────────────────────────────────
# CCA-F Note: Four distinct token types have different prices.
#   input            — regular uncached input tokens
#   output           — generated tokens (most expensive)
#   cache_write      — tokens written to prompt cache (~1.25× input)
#   cache_read       — tokens served from prompt cache (~0.1× input)
# All prices are USD per million tokens ($/MTok).

PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_write": 1.00,
        "cache_read": 0.08,
    },
    "claude-sonnet-4-20250514": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
    "claude-opus-4-20250514": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
}

# Shortname → full ID used for price lookup when model is a shortname
_SHORTNAME_TO_ID: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
}

_FALLBACK_PRICES = PRICING["claude-haiku-4-5-20251001"]


def _resolve_prices(model: str) -> dict[str, float]:
    """Return the pricing dict for a model shortname or full ID."""
    full_id = _SHORTNAME_TO_ID.get(model, model)
    return PRICING.get(full_id, _FALLBACK_PRICES)


class CostTracker:
    """
    Accumulates token usage and cost across all API calls in a session.

    CCA-F — Cost Management:
      Tracks the four token categories separately because they have
      different prices. Cache reads are ~10× cheaper than regular input —
      tracking them explicitly proves the caching strategy is working.

    Phase 2: Also logs tool invocations (separate from API call rows).
    """

    def __init__(self) -> None:
        self._calls: int = 0
        self._input: int = 0
        self._output: int = 0
        self._cache_write: int = 0
        self._cache_read: int = 0
        self._total_cost: float = 0.0
        self._tool_invocations: int = 0

    # ── Core methods ───────────────────────────────────────────────────────────

    def calculate_cost(self, usage, model: str) -> float:
        """
        Compute cost for a single API call from its usage object.
        Does NOT modify session state — pure calculation.
        """
        prices = _resolve_prices(model)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0

        return (
            in_tok / 1_000_000 * prices["input"]
            + out_tok / 1_000_000 * prices["output"]
            + cw / 1_000_000 * prices["cache_write"]
            + cr / 1_000_000 * prices["cache_read"]
        )

    def add_call(self, usage, model: str) -> float:
        """
        Record a completed API call.
        Accumulates session totals, logs to CSV, and returns call cost.
        """
        cost = self.calculate_cost(usage, model)

        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0

        self._calls += 1
        self._input += in_tok
        self._output += out_tok
        self._cache_write += cw
        self._cache_read += cr
        self._total_cost += cost

        self._log_api_call(model, in_tok, out_tok, cw, cr, cost)
        return cost

    def session_total(self) -> float:
        """Return total session cost in USD."""
        return self._total_cost

    def log_tool_call(
        self,
        tool_name: str,
        inputs: dict,
        result: dict,
        elapsed_ms: int,
    ) -> None:
        """
        Log a tool invocation to usage.log.

        CCA-F Domain 4 — Tool Use:
          Tool calls don't cost API tokens directly, but they're part of
          the agentic loop that does. Logging them helps correlate tool
          activity with API cost in post-session analysis.

        Format: [TOOL] <name> | <key>=<val> | ok/error | <ms>ms
        """
        self._tool_invocations += 1

        # Build a compact input summary (avoid logging secrets or huge content)
        input_parts = []
        for k, v in (inputs or {}).items():
            v_str = str(v)
            if len(v_str) > 40:
                v_str = v_str[:37] + "..."
            input_parts.append(f"{k}={v_str}")
        input_summary = ", ".join(input_parts) if input_parts else "(no inputs)"

        # Result summary: ok or error + size
        if "error" in result:
            result_summary = f"error: {str(result['error'])[:50]}"
        else:
            result_summary = f"ok ({len(str(result))} chars)"

        line = (
            f"{datetime.utcnow().isoformat()} "
            f"[TOOL] {tool_name} | {input_summary} | {result_summary} | {elapsed_ms}ms\n"
        )

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # non-fatal

    # ── Display ────────────────────────────────────────────────────────────────

    def display_summary(self) -> None:
        """
        Print a Rich-formatted session summary table.

        CCA-F — Cost Management:
          Separating cache_write vs cache_read in the summary lets you
          verify that your prompt-caching strategy is paying off.
          High cache_read / low cache_write = efficient caching.
        """
        if self._calls == 0 and self._tool_invocations == 0:
            console.print("[dim]No API calls made in this session.[/dim]")
            return

        table = Table(
            title="[bold cyan]Session Summary[/bold cyan]",
            border_style="cyan",
            show_header=True,
        )
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="cyan")

        table.add_row("API calls", str(self._calls))
        table.add_row("Tool invocations", str(self._tool_invocations))
        table.add_row("Input tokens", f"{self._input:,}")
        table.add_row("Output tokens", f"{self._output:,}")
        table.add_row("Cache writes", f"{self._cache_write:,}")
        table.add_row("Cache reads", f"{self._cache_read:,}")
        table.add_row("─" * 22, "─" * 12)
        table.add_row(
            "[bold green]Total cost[/bold green]",
            f"[bold green]${self._total_cost:.5f} USD[/bold green]",
        )

        console.print(table)

        if self._cache_read > 0:
            total_input = self._input + self._cache_read
            savings_pct = (self._cache_read / max(total_input, 1)) * 100
            console.print(
                f"[dim green]Tip: Cache hit rate: {savings_pct:.1f}% of input served from cache "
                f"(~10x cheaper than regular input)[/dim green]"
            )

    def warn_if_over_threshold(self, threshold: float) -> None:
        """
        Print a yellow warning if the session cost exceeds `threshold` USD.

        CCA-F — Cost Management: real applications should alert users
        when costs approach budget limits.
        """
        if self._total_cost >= threshold:
            console.print(
                f"[yellow](!) Session cost [bold]${self._total_cost:.5f}[/bold] "
                f"has reached the ${threshold:.2f} warning threshold.[/yellow]"
            )

    # ── CSV logging (API calls) ────────────────────────────────────────────────

    def _log_api_call(
        self,
        model: str,
        in_tok: int,
        out_tok: int,
        cw: int,
        cr: int,
        cost: float,
    ) -> None:
        """Append one CSV row to the usage log for an API call."""
        write_header = not os.path.exists(LOG_FILE)
        try:
            with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(
                        [
                            "timestamp",
                            "type",
                            "model",
                            "input_tokens",
                            "output_tokens",
                            "cache_write",
                            "cache_read",
                            "cost_usd",
                        ]
                    )
                writer.writerow(
                    [
                        datetime.utcnow().isoformat(),
                        "api_call",
                        model,
                        in_tok,
                        out_tok,
                        cw,
                        cr,
                        f"{cost:.6f}",
                    ]
                )
        except OSError:
            pass  # non-fatal — never crash the CLI over a logging failure
