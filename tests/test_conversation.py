"""
tests/test_conversation.py — Unit tests for core/conversation.py

CCA-F Domain: Context Management
"""

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.conversation import ConversationManager, SUMMARIZE_THRESHOLD, KEEP_RECENT


class TestAddAndGetMessages:
    def setup_method(self):
        self.mgr = ConversationManager()

    def test_starts_empty(self):
        assert self.mgr.get_history() == []
        assert self.mgr.message_count() == 0

    def test_add_string_message(self):
        self.mgr.add_message("user", "Hello")
        history = self.mgr.get_history()
        assert len(history) == 1
        assert history[0] == {"role": "user", "content": "Hello"}

    def test_add_multiple_messages(self):
        self.mgr.add_message("user", "Hi")
        self.mgr.add_message("assistant", "Hello!")
        assert self.mgr.message_count() == 2

    def test_add_list_content_for_tool_use(self):
        # Phase 2: content can be a list of blocks
        blocks = [{"type": "tool_use", "id": "abc", "name": "calculator", "input": {}}]
        self.mgr.add_message("assistant", blocks)
        history = self.mgr.get_history()
        assert history[0]["content"] == blocks

    def test_get_history_returns_copy(self):
        self.mgr.add_message("user", "test")
        h1 = self.mgr.get_history()
        h1.append({"role": "user", "content": "injected"})
        # Internal state must not be modified
        assert self.mgr.message_count() == 1


class TestClearAndReplace:
    def setup_method(self):
        self.mgr = ConversationManager()

    def test_clear_empties_history(self):
        self.mgr.add_message("user", "Hello")
        self.mgr.clear()
        assert self.mgr.message_count() == 0

    def test_replace_history(self):
        self.mgr.add_message("user", "old message")
        new_history = [{"role": "user", "content": "brand new"}]
        self.mgr.replace_history(new_history)
        assert self.mgr.message_count() == 1
        assert self.mgr.get_history()[0]["content"] == "brand new"

    def test_replace_history_is_independent_copy(self):
        new_history = [{"role": "user", "content": "msg"}]
        self.mgr.replace_history(new_history)
        new_history.clear()
        assert self.mgr.message_count() == 1


class TestTokenEstimate:
    def setup_method(self):
        self.mgr = ConversationManager()

    def test_empty_history_is_zero(self):
        assert self.mgr.get_token_estimate() == 0

    def test_estimate_grows_with_content(self):
        self.mgr.add_message("user", "a" * 400)
        estimate = self.mgr.get_token_estimate()
        assert estimate == 100  # 400 chars / 4

    def test_estimate_handles_list_content(self):
        # get_token_estimate sums len(str(block["text"])) for list content, then // 4
        # {"text": "a" * 200} → 200 chars → 200 // 4 = 50 tokens
        self.mgr.add_message("assistant", [{"text": "a" * 200}])
        estimate = self.mgr.get_token_estimate()
        assert estimate == 50


class TestSummarizeIfNeeded:
    def setup_method(self):
        self.mgr = ConversationManager()

    def _fill_history(self, n):
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            self.mgr.add_message(role, f"Message {i}")

    def test_no_summarize_below_threshold(self):
        self._fill_history(SUMMARIZE_THRESHOLD - 1)
        client = MagicMock()
        result = self.mgr.summarize_if_needed(client)
        assert result is False
        client.summarize.assert_not_called()

    def test_summarize_fires_at_threshold(self):
        self._fill_history(SUMMARIZE_THRESHOLD)
        client = MagicMock()
        client.summarize.return_value = "Summary of earlier conversation."
        result = self.mgr.summarize_if_needed(client)
        assert result is True
        client.summarize.assert_called_once()

    def test_after_summarize_history_is_shorter(self):
        self._fill_history(SUMMARIZE_THRESHOLD)
        client = MagicMock()
        client.summarize.return_value = "Compact summary."
        self.mgr.summarize_if_needed(client)
        # Summary placeholder (2 msgs) + KEEP_RECENT recent messages
        assert self.mgr.message_count() == 2 + KEEP_RECENT

    def test_recent_messages_preserved_verbatim(self):
        self._fill_history(SUMMARIZE_THRESHOLD)
        # Note the last message before summarization
        last_msg = self.mgr.get_history()[-1]["content"]
        client = MagicMock()
        client.summarize.return_value = "Summary."
        self.mgr.summarize_if_needed(client)
        # The final message should still appear in the new history
        history = self.mgr.get_history()
        contents = [m["content"] for m in history]
        assert last_msg in contents


class TestPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        mgr = ConversationManager()
        mgr.add_message("user", "Hello")
        mgr.add_message("assistant", "Hi there!")
        filepath = str(tmp_path / "conversation.json")
        mgr.save_to_file(filepath)

        mgr2 = ConversationManager()
        mgr2.load_from_file(filepath)
        assert mgr2.get_history() == mgr.get_history()

    def test_saved_file_is_valid_json(self, tmp_path):
        mgr = ConversationManager()
        mgr.add_message("user", "test")
        filepath = str(tmp_path / "conv.json")
        mgr.save_to_file(filepath)
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_load_invalid_format_raises(self, tmp_path):
        filepath = str(tmp_path / "bad.json")
        Path(filepath).write_text('{"not": "a list"}', encoding="utf-8")
        mgr = ConversationManager()
        with pytest.raises(ValueError):
            mgr.load_from_file(filepath)
