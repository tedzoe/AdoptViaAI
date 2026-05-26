"""
tests/test_cost_tracker.py — Unit tests for core/cost_tracker.py

CCA-F Domain: Cost Management
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.cost_tracker import CostTracker, PRICING, _resolve_prices


class TestResolvePrices:
    def test_shortname_haiku_resolves(self):
        prices = _resolve_prices("haiku")
        assert "input" in prices
        assert "output" in prices
        assert prices["output"] > prices["input"]

    def test_full_model_id_resolves(self):
        full_id = "claude-haiku-4-5-20251001"
        prices = _resolve_prices(full_id)
        assert prices == PRICING[full_id]

    def test_unknown_model_falls_back_to_haiku(self):
        prices = _resolve_prices("claude-nonexistent-model")
        # Falls back to haiku prices
        assert prices == PRICING["claude-haiku-4-5-20251001"]


class TestCostTrackerCalculateCost:
    def setup_method(self):
        self.tracker = CostTracker()

    def _make_usage(self, input=0, output=0, cache_write=0, cache_read=0):
        usage = MagicMock()
        usage.input_tokens = input
        usage.output_tokens = output
        usage.cache_creation_input_tokens = cache_write
        usage.cache_read_input_tokens = cache_read
        return usage

    def test_zero_tokens_costs_zero(self):
        usage = self._make_usage()
        cost = self.tracker.calculate_cost(usage, "haiku")
        assert cost == 0.0

    def test_output_tokens_cost_more_than_input(self):
        input_usage = self._make_usage(input=1_000_000)
        output_usage = self._make_usage(output=1_000_000)
        input_cost = self.tracker.calculate_cost(input_usage, "haiku")
        output_cost = self.tracker.calculate_cost(output_usage, "haiku")
        assert output_cost > input_cost

    def test_cache_read_cheaper_than_input(self):
        input_usage = self._make_usage(input=1_000_000)
        cached_usage = self._make_usage(cache_read=1_000_000)
        input_cost = self.tracker.calculate_cost(input_usage, "haiku")
        cached_cost = self.tracker.calculate_cost(cached_usage, "haiku")
        assert cached_cost < input_cost

    def test_haiku_cheaper_than_sonnet(self):
        usage = self._make_usage(input=1_000_000, output=1_000_000)
        haiku_cost = self.tracker.calculate_cost(usage, "haiku")
        sonnet_cost = self.tracker.calculate_cost(usage, "sonnet")
        assert haiku_cost < sonnet_cost


class TestCostTrackerAddCall:
    def setup_method(self):
        self.tracker = CostTracker()

    def _make_usage(self, input=100, output=50, cache_write=0, cache_read=0):
        usage = MagicMock()
        usage.input_tokens = input
        usage.output_tokens = output
        usage.cache_creation_input_tokens = cache_write
        usage.cache_read_input_tokens = cache_read
        return usage

    def test_add_call_accumulates_session_total(self):
        usage = self._make_usage()
        with patch.object(self.tracker, "_log_api_call"):
            cost1 = self.tracker.add_call(usage, "haiku")
            cost2 = self.tracker.add_call(usage, "haiku")
        assert self.tracker.session_total() == pytest.approx(cost1 + cost2, rel=1e-6)

    def test_session_total_starts_at_zero(self):
        assert self.tracker.session_total() == 0.0

    def test_add_call_increments_call_count(self):
        usage = self._make_usage()
        with patch.object(self.tracker, "_log_api_call"):
            self.tracker.add_call(usage, "haiku")
            self.tracker.add_call(usage, "haiku")
        assert self.tracker._calls == 2


class TestCostTrackerWarnThreshold:
    def setup_method(self):
        self.tracker = CostTracker()

    def _make_usage(self, input=0, output=1_000_000):
        usage = MagicMock()
        usage.input_tokens = input
        usage.output_tokens = output
        usage.cache_creation_input_tokens = 0
        usage.cache_read_input_tokens = 0
        return usage

    def test_warn_below_threshold_does_not_print(self):
        # 100 output tokens of haiku ≈ $0.0000004 — far below a $10 threshold
        with patch.object(self.tracker, "_log_api_call"):
            self.tracker.add_call(self._make_usage(output=100), "haiku")
        with patch("core.cost_tracker.console") as mock_console:
            self.tracker.warn_if_over_threshold(10.0)
            mock_console.print.assert_not_called()

    def test_warn_above_threshold_prints_warning(self):
        # 10M output tokens of opus ≈ $750 — far above a $0.01 threshold
        with patch.object(self.tracker, "_log_api_call"):
            self.tracker.add_call(self._make_usage(output=10_000_000), "opus")
        with patch("core.cost_tracker.console") as mock_console:
            self.tracker.warn_if_over_threshold(0.01)
            mock_console.print.assert_called_once()
