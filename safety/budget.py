"""
safety/budget.py — Phase 5: Cost Budget & Rate Limiting

CCA-F Domain: Safety & Responsible Use
This module guards the COST layer — every API call is metered against a
per-session USD cap and a requests-per-minute limit BEFORE the call is made.

Design principles:
  - check_or_raise() is called BEFORE the API request so the spending
    boundary is always honoured; we never overshoot.
  - Pricing is intentionally conservative (uses output-token rate for
    unknown models) so estimates err on the side of caution.
  - Rate limiting uses a sliding window (collections.deque) rather than
    a fixed window to avoid burst traffic at window boundaries.
  - All limits default to generous values that will not interfere with
    normal interactive use; they are intended to catch runaway loops.

Configuration via environment variables (read once at module import):
  AVAI_MAX_USD   Maximum cumulative USD spend per session (default: 1.00)
  AVAI_MAX_RPM   Maximum API requests per minute (default: 60)

CCA-F Note:
  BudgetEnforcer is stateful and process-scoped.  In a web service you
  would store BudgetState in the user's session rather than as a module
  global.  For a CLI tool, one enforcer per process is the right model.
"""

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ── Environment-based defaults ─────────────────────────────────────────────────
_ENV_MAX_USD: float = float(os.getenv("AVAI_MAX_USD", "1.00"))
_ENV_MAX_RPM: int   = int(os.getenv("AVAI_MAX_RPM", "60"))

# ── Token pricing table ────────────────────────────────────────────────────────
# USD per 1,000,000 tokens (input / output / cache_write / cache_read).
# Keep in sync with core/cost_tracker.py PRICING table.
#
# CCA-F Note: Budgeting uses only input and output pricing (the dominant
# costs).  Cache write/read deltas are small enough to ignore for budget
# estimation purposes.
_PRICING: dict[str, dict[str, float]] = {
    # Haiku — cheapest, used for development
    # Keep in sync with core/cost_tracker.py PRICING table (full versioned IDs).
    "claude-haiku-4-5-20251001": {
        "input":  0.80,
        "output": 4.00,
    },
    # Sonnet — balanced quality/cost
    "claude-sonnet-4-20250514": {
        "input":  3.00,
        "output": 15.00,
    },
    # Opus — highest quality
    "claude-opus-4-20250514": {
        "input":  15.00,
        "output": 75.00,
    },
}

# Friendly short-names mapped to full model IDs (mirrors core/client.py)
_MODEL_ALIASES: dict[str, str] = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-20250514",
    "opus":   "claude-opus-4-20250514",
}

# Fallback pricing when the model is not in the table
_FALLBACK_PRICING = {"input": 15.00, "output": 75.00}  # conservative (opus rate)


# ── Helper ─────────────────────────────────────────────────────────────────────

def _resolve_pricing(model: str) -> dict[str, float]:
    """Return the pricing dict for model, resolving aliases, with fallback."""
    resolved = _MODEL_ALIASES.get(model, model)
    return _PRICING.get(resolved, _FALLBACK_PRICING)


def _token_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """
    Compute the USD cost for a single API call.

    CCA-F Note: tokens are billed per 1M so we divide by 1_000_000.
    """
    p = _resolve_pricing(model)
    return (
        input_tokens  / 1_000_000 * p["input"] +
        output_tokens / 1_000_000 * p["output"]
    )


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class BudgetState:
    """
    Snapshot of budget usage at a point in time.

    Attributes:
        total_usd       Cumulative USD spent this session.
        total_calls     Number of API calls made this session.
        remaining_usd   How much budget is left (max_usd - total_usd).
        rpm_current     Requests made in the last 60 seconds.
        max_usd         The configured USD ceiling.
        max_rpm         The configured RPM ceiling.
        warn_at_pct     Fraction at which a warning is emitted (e.g. 0.80).
        warning_issued  True if the warning threshold has been crossed.
    """
    total_usd:     float = 0.0
    total_calls:   int   = 0
    remaining_usd: float = 0.0
    rpm_current:   int   = 0
    max_usd:       float = 0.0
    max_rpm:       int   = 0
    warn_at_pct:   float = 0.80
    warning_issued: bool = False


@dataclass
class BudgetCheckResult:
    """
    Return value of BudgetEnforcer.check().

    Attributes:
        allowed True if the next API call is permitted.
        reason  Empty string when allowed; human-readable explanation otherwise.
        state   A BudgetState snapshot at the time of the check.
    """
    allowed: bool
    reason:  str
    state:   BudgetState


class BudgetExceededError(Exception):
    """
    Raised by BudgetEnforcer.check_or_raise() when a call would exceed limits.

    CCA-F Note:
        Raise this BEFORE the API call.  Never catch it silently — the whole
        point is that the budget boundary is always respected.  Surface it
        to the user with a clear, actionable message.
    """
    def __init__(self, reason: str, state: BudgetState) -> None:
        super().__init__(reason)
        self.reason = reason
        self.state = state


# ── BudgetEnforcer ─────────────────────────────────────────────────────────────

class BudgetEnforcer:
    """
    Per-session cost and rate-limit enforcer.

    Usage:
        enforcer = BudgetEnforcer(max_usd=0.50, max_rpm=30)

        # Before every API call:
        enforcer.check_or_raise()  # raises BudgetExceededError if over limit

        # After every API call:
        cost = enforcer.record(input_tokens=150, output_tokens=300, model="haiku")

    CCA-F Note:
        Instantiate once per session (e.g. at the top of main.py).
        Do NOT create a new BudgetEnforcer per API call — the session
        state would reset and limits would never trigger.
    """

    def __init__(
        self,
        max_usd:      float = _ENV_MAX_USD,
        max_rpm:      int   = _ENV_MAX_RPM,
        warn_at_pct:  float = 0.80,
    ) -> None:
        """
        Args:
            max_usd:     USD ceiling for this session (default: AVAI_MAX_USD env var).
            max_rpm:     Max API requests per minute (default: AVAI_MAX_RPM env var).
            warn_at_pct: Emit a warning when (total_usd / max_usd) >= this fraction.
        """
        self._max_usd     = max_usd
        self._max_rpm     = max_rpm
        self._warn_at_pct = warn_at_pct

        self._total_usd:    float = 0.0
        self._total_calls:  int   = 0
        self._warning_done: bool  = False

        # Sliding window rate limiter: stores POSIX timestamps of recent calls.
        # We keep only the timestamps from the last 60 seconds.
        self._call_timestamps: deque[float] = deque()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _current_rpm(self) -> int:
        """Return the number of API calls made in the last 60 seconds."""
        now = time.monotonic()
        cutoff = now - 60.0
        # Drop expired entries from the left of the deque
        while self._call_timestamps and self._call_timestamps[0] < cutoff:
            self._call_timestamps.popleft()
        return len(self._call_timestamps)

    def _snapshot(self) -> BudgetState:
        return BudgetState(
            total_usd=self._total_usd,
            total_calls=self._total_calls,
            remaining_usd=max(0.0, self._max_usd - self._total_usd),
            rpm_current=self._current_rpm(),
            max_usd=self._max_usd,
            max_rpm=self._max_rpm,
            warn_at_pct=self._warn_at_pct,
            warning_issued=self._warning_done,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self) -> BudgetCheckResult:
        """
        Check whether the next API call is permitted.

        Does NOT record the call — call record() after the API responds.
        Safe to call multiple times without side effects.

        Returns:
            BudgetCheckResult with allowed=True if the call may proceed.
        """
        state = self._snapshot()

        # USD budget check
        if self._total_usd >= self._max_usd:
            return BudgetCheckResult(
                allowed=False,
                reason=(
                    f"Session budget exceeded: ${self._total_usd:.4f} spent "
                    f"of ${self._max_usd:.4f} limit. "
                    f"Set AVAI_MAX_USD to increase the limit."
                ),
                state=state,
            )

        # Rate limit check
        rpm = self._current_rpm()
        if rpm >= self._max_rpm:
            return BudgetCheckResult(
                allowed=False,
                reason=(
                    f"Rate limit reached: {rpm}/{self._max_rpm} requests/minute. "
                    f"Wait a moment and try again, or set AVAI_MAX_RPM to increase."
                ),
                state=state,
            )

        return BudgetCheckResult(allowed=True, reason="", state=state)

    def check_or_raise(self) -> BudgetState:
        """
        Call before every API request.  Raises BudgetExceededError if blocked.

        Returns:
            BudgetState snapshot (for informational use by callers).

        Raises:
            BudgetExceededError if the USD budget or RPM limit is exceeded.

        CCA-F Note:
            Always call this BEFORE the API call.  The spend is recorded
            AFTER via record() once we know the actual token counts.
        """
        result = self.check()
        if not result.allowed:
            raise BudgetExceededError(result.reason, result.state)
        return result.state

    def record(
        self,
        input_tokens:  int,
        output_tokens: int,
        model:         str,
    ) -> float:
        """
        Record a completed API call and update running totals.

        Args:
            input_tokens:  Tokens in the request (from response.usage.input_tokens).
            output_tokens: Tokens in the response (from response.usage.output_tokens).
            model:         Model shortname or full ID (e.g. "haiku" or
                           "claude-haiku-4-5").

        Returns:
            The USD cost of this call (for the caller to log/display).

        CCA-F Note:
            Call this AFTER a successful API response.  If the API call
            fails, do not call record() — no tokens were consumed.
        """
        cost = _token_cost(input_tokens, output_tokens, model)
        self._total_usd   += cost
        self._total_calls += 1
        self._call_timestamps.append(time.monotonic())
        return cost

    def warn_threshold_reached(self) -> bool:
        """
        Return True (once) when cumulative spend crosses the warning threshold.

        Subsequent calls return False until the threshold is crossed again
        (which can only happen if max_usd is changed, so effectively it fires
        at most once per session).

        CCA-F Note:
            Call this AFTER record() so the threshold check reflects the
            updated total.  Emit a warning to the user if it returns True.
        """
        pct = self._total_usd / self._max_usd if self._max_usd > 0 else 0.0
        if not self._warning_done and pct >= self._warn_at_pct:
            self._warning_done = True
            return True
        return False

    def summary(self) -> dict:
        """
        Return a dict summarising the current budget state.

        Suitable for display in 'avai safety status' and for audit logging.
        """
        rpm = self._current_rpm()
        pct = (self._total_usd / self._max_usd * 100) if self._max_usd > 0 else 0.0
        return {
            "total_usd":     round(self._total_usd, 6),
            "max_usd":       self._max_usd,
            "remaining_usd": round(max(0.0, self._max_usd - self._total_usd), 6),
            "used_pct":      round(pct, 1),
            "total_calls":   self._total_calls,
            "rpm_current":   rpm,
            "max_rpm":       self._max_rpm,
            "warn_at_pct":   self._warn_at_pct,
            "warning_issued": self._warning_done,
        }

    def reset(self) -> None:
        """
        Reset all counters.  Useful in tests; not called during normal use.

        CCA-F Note:
            Resetting mid-session would defeat the purpose of the budget cap.
            Only use this in unit tests or when the user explicitly starts a
            new session context.
        """
        self._total_usd     = 0.0
        self._total_calls   = 0
        self._warning_done  = False
        self._call_timestamps.clear()
