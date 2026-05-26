"""
tests/test_budget.py — Unit tests for safety/budget.py

CCA-F Domain: Safety & Responsible Use
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from safety.budget import BudgetEnforcer, BudgetExceededError, _token_cost


class TestTokenCost:
    def test_haiku_cost_is_cheap(self):
        cost = _token_cost(1_000_000, 0, "claude-haiku-4-5")
        assert cost == pytest.approx(1.00)

    def test_output_tokens_cost_more_than_input(self):
        input_cost = _token_cost(1_000_000, 0, "haiku")
        output_cost = _token_cost(0, 1_000_000, "haiku")
        assert output_cost > input_cost

    def test_unknown_model_uses_fallback(self):
        # Fallback is conservative (opus rate) — should not raise
        cost = _token_cost(100, 100, "claude-unknown-model")
        assert cost > 0

    def test_zero_tokens_cost_zero(self):
        assert _token_cost(0, 0, "haiku") == 0.0


class TestBudgetEnforcerCheck:
    def test_fresh_enforcer_allows_call(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        result = enforcer.check()
        assert result.allowed is True
        assert result.reason == ""

    def test_exceeded_budget_blocks_call(self):
        enforcer = BudgetEnforcer(max_usd=0.001, max_rpm=60)
        # Simulate a call that consumes the budget
        enforcer.record(input_tokens=500_000, output_tokens=200_000, model="haiku")
        result = enforcer.check()
        assert result.allowed is False
        assert "budget exceeded" in result.reason.lower()

    def test_check_or_raise_raises_when_blocked(self):
        enforcer = BudgetEnforcer(max_usd=0.0001, max_rpm=60)
        enforcer.record(input_tokens=100_000, output_tokens=100_000, model="haiku")
        with pytest.raises(BudgetExceededError):
            enforcer.check_or_raise()

    def test_check_does_not_record_call(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        enforcer.check()
        enforcer.check()
        # No calls recorded — total_calls should still be 0
        assert enforcer.summary()["total_calls"] == 0


class TestBudgetEnforcerRecord:
    def test_record_accumulates_cost(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        cost1 = enforcer.record(1000, 500, "haiku")
        cost2 = enforcer.record(1000, 500, "haiku")
        summary = enforcer.summary()
        assert summary["total_calls"] == 2
        assert summary["total_usd"] == pytest.approx(cost1 + cost2, rel=1e-6)

    def test_record_returns_call_cost(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        cost = enforcer.record(1_000_000, 0, "haiku")
        assert cost == pytest.approx(1.00)  # haiku input price is $1.00/MTok

    def test_reset_clears_state(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        enforcer.record(1000, 500, "haiku")
        enforcer.reset()
        summary = enforcer.summary()
        assert summary["total_calls"] == 0
        assert summary["total_usd"] == 0.0


class TestBudgetEnforcerWarnThreshold:
    def test_warn_threshold_fires_once(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60, warn_at_pct=0.80)
        # Spend 85% of budget
        enforcer.record(input_tokens=850_000, output_tokens=0, model="haiku")
        assert enforcer.warn_threshold_reached() is True
        # Second call should return False (already fired)
        assert enforcer.warn_threshold_reached() is False

    def test_warn_threshold_does_not_fire_below_pct(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60, warn_at_pct=0.80)
        enforcer.record(input_tokens=100_000, output_tokens=0, model="haiku")
        assert enforcer.warn_threshold_reached() is False


class TestBudgetEnforcerSummary:
    def test_summary_contains_expected_keys(self):
        enforcer = BudgetEnforcer(max_usd=0.50, max_rpm=30)
        summary = enforcer.summary()
        for key in ("total_usd", "max_usd", "remaining_usd", "used_pct",
                    "total_calls", "rpm_current", "max_rpm"):
            assert key in summary

    def test_remaining_usd_decreases_after_record(self):
        enforcer = BudgetEnforcer(max_usd=1.00, max_rpm=60)
        before = enforcer.summary()["remaining_usd"]
        enforcer.record(1_000_000, 0, "haiku")
        after = enforcer.summary()["remaining_usd"]
        assert after < before
