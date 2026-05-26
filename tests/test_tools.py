"""
tests/test_tools.py — Unit tests for tools/builtin.py

CCA-F Domain: Tool Use & Function Calling
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tools.builtin import (
    calculator_handler,
    file_reader_handler,
    save_note_handler,
    get_project_info_handler,
    _safe_calculate,
)


class TestSafeCalculate:
    def test_basic_addition(self):
        assert _safe_calculate("2 + 2") == 4.0

    def test_order_of_operations(self):
        assert _safe_calculate("(100 + 50) * 2") == 300.0

    def test_integer_division(self):
        assert _safe_calculate("10 // 3") == 3.0

    def test_modulo(self):
        assert _safe_calculate("10 % 3") == 1.0

    def test_exponentiation(self):
        assert _safe_calculate("2 ** 10") == 1024.0

    def test_unary_negation(self):
        assert _safe_calculate("-5 + 10") == 5.0

    def test_rejects_string_constants(self):
        with pytest.raises(ValueError):
            _safe_calculate("'hello'")

    def test_rejects_function_calls(self):
        with pytest.raises((ValueError, SyntaxError)):
            _safe_calculate("__import__('os').system('whoami')")

    def test_division_by_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            _safe_calculate("1 / 0")


class TestCalculatorHandler:
    def test_returns_result_for_valid_expression(self):
        result = calculator_handler({"expression": "6 * 7"})
        assert result["result"] == 42
        assert result["expression"] == "6 * 7"

    def test_returns_int_for_whole_number_result(self):
        result = calculator_handler({"expression": "10 / 2"})
        assert isinstance(result["result"], int)
        assert result["result"] == 5

    def test_returns_float_for_fractional_result(self):
        result = calculator_handler({"expression": "10 / 3"})
        assert isinstance(result["result"], float)

    def test_empty_expression_returns_error(self):
        result = calculator_handler({"expression": ""})
        assert "error" in result

    def test_missing_expression_returns_error(self):
        result = calculator_handler({})
        assert "error" in result

    def test_invalid_syntax_returns_error(self):
        result = calculator_handler({"expression": "2 +"})
        assert "error" in result


class TestFileReaderHandler:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "sample.txt"
        f.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")
        result = file_reader_handler({"filepath": str(f)})
        assert "content" in result
        assert "line 1" in result["content"]
        assert result["lines_read"] == 3

    def test_max_lines_respected(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
        result = file_reader_handler({"filepath": str(f), "max_lines": 10})
        assert result["lines_read"] == 10
        assert result["truncated"] is True

    def test_missing_file_returns_error(self):
        result = file_reader_handler({"filepath": "/nonexistent/path/file.txt"})
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestSaveNoteHandler:
    def test_saves_file_to_notes_dir(self, tmp_path, monkeypatch):
        import tools.builtin as builtin_mod
        monkeypatch.setattr(builtin_mod, "_NOTES_DIR", tmp_path)
        result = save_note_handler({"filename": "test.txt", "content": "hello"})
        assert result["saved"] is True
        assert (tmp_path / "test.txt").read_text() == "hello"

    def test_append_mode(self, tmp_path, monkeypatch):
        import tools.builtin as builtin_mod
        monkeypatch.setattr(builtin_mod, "_NOTES_DIR", tmp_path)
        save_note_handler({"filename": "note.txt", "content": "first"})
        save_note_handler({"filename": "note.txt", "content": " second", "append": True})
        assert (tmp_path / "note.txt").read_text() == "first second"

    def test_empty_filename_returns_error(self):
        result = save_note_handler({"filename": "", "content": "data"})
        assert result["saved"] is False
        assert "error" in result


class TestGetProjectInfoHandler:
    def test_returns_dict_with_expected_keys(self):
        result = get_project_info_handler({})
        for key in ("name", "version", "tools_available", "commands_available"):
            assert key in result

    def test_name_is_adoptviaai(self):
        result = get_project_info_handler({})
        assert result["name"] == "AdoptviaAI"

    def test_tools_list_contains_calculator(self):
        result = get_project_info_handler({})
        assert "calculator" in result["tools_available"]
