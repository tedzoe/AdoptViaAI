"""
safety/filter.py — Phase 5: Output Filtering

CCA-F Domain: Safety & Responsible Use
This module guards the OUTPUT layer — Claude's response is scanned and
redacted AFTER it is received and BEFORE it is shown to the user or
persisted to disk.

Why filter output at all?

  Claude may inadvertently reproduce sensitive data that appeared in:
    - The conversation history (a user pasted a snippet containing credentials)
    - A file that was read via the file_reader tool
    - An MCP server result
    - The system prompt (if it contained environment-specific config)

  Output filtering provides a last-resort backstop so those values do not
  end up in terminal output, log files, or saved conversation JSON.

Security note:
  Regex-based redaction is best-effort — it catches obvious patterns.
  It is NOT a security boundary.  Credentials and PII that appear in
  unusual formats, or that are obfuscated, will not be caught.
  Do not rely on this module as your sole defence against data leakage.

CCA-F Note:
  filter_output() is called AFTER every API response in --safe mode.
  The redaction is non-destructive: FilterResult retains the original
  length and a count of redactions so the caller can decide whether to
  warn the user that output was modified.
"""

import re
from dataclasses import dataclass, field
from typing import Any

# ── Rule and result types ──────────────────────────────────────────────────────

@dataclass
class _Rule:
    """
    A single redaction rule.

    Attributes:
        label       Short name used in redaction placeholders and logs.
        pattern     Compiled regex (must have at least one match group if
                    replacement uses back-references, otherwise a plain string).
        replacement Replacement string inserted in place of the match.
                    Use {label} to embed the rule label automatically.
    """
    label: str
    pattern: re.Pattern
    replacement: str


@dataclass
class FilterResult:
    """
    Return value of filter_output().

    Attributes:
        clean           True if no redactions were made.
        text            The redacted text (identical to input if clean=True).
        redactions      List of (label, matched_text) pairs for audit purposes.
        original_length Character count of the unredacted text.
        redacted_length Character count of the redacted text.
    """
    clean: bool
    text: str
    redactions: list[tuple[str, str]] = field(default_factory=list)
    original_length: int = 0
    redacted_length: int = 0


# ── Default rule set ───────────────────────────────────────────────────────────
# Rules are applied in the order listed; earlier rules take priority.
# Patterns are deliberately broad — the goal is to reduce accidental
# leakage, not to achieve perfect recall at the cost of false positives.

def _build_default_rules() -> list[_Rule]:
    """
    Build and return the default list of redaction rules.

    CCA-F Note:
        Rules are built at module-import time and cached in _DEFAULT_RULES.
        To customise the rule set for your application, pass a rules=
        argument to filter_output() or call _build_default_rules() and
        modify the list before passing it.
    """
    def rule(label: str, pattern: str, replacement: str | None = None) -> _Rule:
        if replacement is None:
            replacement = f"[{label.upper()}_REDACTED]"
        return _Rule(
            label=label,
            pattern=re.compile(pattern, re.IGNORECASE | re.MULTILINE),
            replacement=replacement,
        )

    return [
        # ── Credentials ──────────────────────────────────────────────────────
        rule(
            "api_key",
            r"sk-ant-[a-zA-Z0-9\-_]{20,}",
            "[ANTHROPIC_KEY_REDACTED]",
        ),
        rule(
            "api_key",
            r"(?<![a-zA-Z0-9])sk-[a-zA-Z0-9]{20,}",
            "[API_KEY_REDACTED]",
        ),
        rule(
            "aws_key",
            r"AKIA[A-Z0-9]{16}",
            "[AWS_KEY_REDACTED]",
        ),
        rule(
            "jwt",
            r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}",
            "[JWT_REDACTED]",
        ),
        rule(
            "private_key",
            r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----[\s\S]+?-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
            "[PRIVATE_KEY_REDACTED]",
        ),
        rule(
            "github_token",
            r"ghp_[a-zA-Z0-9]{36}",
            "[GITHUB_TOKEN_REDACTED]",
        ),

        # ── PII ───────────────────────────────────────────────────────────────
        rule(
            "ssn",
            # US Social Security Number: ddd-dd-dddd
            r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
            "[SSN_REDACTED]",
        ),
        rule(
            "credit_card",
            # Major card BINs: Visa (4), Mastercard (5[1-5] or 2221-2720),
            # Amex (34/37), Discover (6011/65)
            # Matches 13-19 digit numbers with optional spaces/hyphens
            r"\b(?:4[0-9]{12}(?:[0-9]{3,6})?|"                # Visa
            r"(?:5[1-5][0-9]{2}|222[1-9]|22[3-9][0-9]|"
            r"2[3-6][0-9]{2}|27[01][0-9]|2720)[0-9]{12}|"     # Mastercard
            r"3[47][0-9]{13}|"                                   # Amex
            r"6(?:011|5[0-9]{2})[0-9]{12,15})"                  # Discover
            r"(?:[-\s]?[0-9]{4}){0,2}\b",
            "[CREDIT_CARD_REDACTED]",
        ),
        rule(
            "email",
            r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",
            "[EMAIL_REDACTED]",
        ),
        rule(
            "phone_us",
            # US phone: (ddd) ddd-dddd, ddd-ddd-dddd, +1 ddd ddd dddd, etc.
            r"(?:\+1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b",
            "[PHONE_REDACTED]",
        ),
        rule(
            "ipv4",
            # IPv4 address — only redact if preceded/followed by non-digit
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
            "[IPV4_REDACTED]",
        ),
    ]


# Module-level cache of default rules so they're only compiled once.
_DEFAULT_RULES: list[_Rule] = _build_default_rules()

# Label → rule index map for fast lookup when a caller requests a subset.
_RULE_MAP: dict[str, list[_Rule]] = {}
for _r in _DEFAULT_RULES:
    _RULE_MAP.setdefault(_r.label, []).append(_r)


# ── Public API ─────────────────────────────────────────────────────────────────

def filter_output(
    text: str,
    rules: list[str] | None = None,
) -> FilterResult:
    """
    Scan text and replace sensitive patterns with redaction placeholders.

    Args:
        text:   The text to filter (typically Claude's response).
        rules:  Optional list of rule labels to apply (e.g. ["api_key", "email"]).
                If None, all default rules are applied.

    Returns:
        FilterResult.  Check .clean to know whether any redactions were made.

    CCA-F Note:
        This function modifies the text in-place (on the local copy).
        Original content is not logged here — that is the audit module's job.
        The redactions list in the result is suitable for passing to
        Auditor.log_filter_redact().
    """
    if not text:
        return FilterResult(clean=True, text=text, original_length=0, redacted_length=0)

    original_length = len(text)
    redactions: list[tuple[str, str]] = []

    if rules is None:
        active_rules = _DEFAULT_RULES
    else:
        active_rules = []
        for label in rules:
            active_rules.extend(_RULE_MAP.get(label, []))

    result_text = text
    for r in active_rules:
        matches = list(r.pattern.finditer(result_text))
        if matches:
            for match in matches:
                redactions.append((r.label, match.group(0)))
            result_text = r.pattern.sub(r.replacement, result_text)

    return FilterResult(
        clean=len(redactions) == 0,
        text=result_text,
        redactions=redactions,
        original_length=original_length,
        redacted_length=len(result_text),
    )


def filter_tool_result(
    tool_result: dict[str, Any],
    rules: list[str] | None = None,
) -> tuple[dict[str, Any], list[FilterResult]]:
    """
    Filter sensitive data from an Anthropic tool_result message dict.

    Anthropic tool results have the shape:
      {
        "role": "user",
        "content": [
          {
            "type": "tool_result",
            "tool_use_id": "...",
            "content": "..." | [{"type": "text", "text": "..."}, ...]
          }
        ]
      }

    This function walks the content blocks and applies filter_output()
    to all text values it finds.

    Args:
        tool_result: An Anthropic tool_result message dict.
        rules:       Passed through to filter_output().

    Returns:
        (filtered_dict, filter_results) where filter_results has one
        FilterResult per text block that was inspected.

    CCA-F Note:
        Tool results often contain file system content (via file_reader
        or MCP notes_read) that may include credentials or PII from the
        user's environment.  Filtering before logging protects against
        inadvertent data capture in logs/audit.log.
    """
    import copy
    filtered = copy.deepcopy(tool_result)
    filter_results: list[FilterResult] = []

    content = filtered.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    fr = filter_output(inner, rules=rules)
                    block["content"] = fr.text
                    filter_results.append(fr)
                elif isinstance(inner, list):
                    for text_block in inner:
                        if isinstance(text_block, dict) and text_block.get("type") == "text":
                            fr = filter_output(text_block.get("text", ""), rules=rules)
                            text_block["text"] = fr.text
                            filter_results.append(fr)

    return filtered, filter_results


def available_rules() -> list[str]:
    """Return the sorted list of unique rule labels in the default rule set."""
    return sorted(set(r.label for r in _DEFAULT_RULES))
