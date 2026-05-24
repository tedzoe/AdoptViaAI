"""
safety/sanitizer.py — Phase 5: Input Sanitisation

CCA-F Domain: Safety & Responsible Use
This module guards the INPUT layer — text is cleaned and validated BEFORE
it leaves the application and reaches the Claude API.

Two classes of problem are caught here:

  1. Prompt injection — attempts to hijack Claude's behaviour by embedding
     instruction-override phrases in user text.  These are flagged as warnings
     (or blocks in strict mode) because they may be legitimate if the user is
     studying prompt-injection patterns rather than attempting an attack.

  2. Secret leakage — API keys, private keys, JWTs, passwords embedded in
     user text.  These are ALWAYS blocked regardless of strict mode because
     sending credentials to a third-party API endpoint is never acceptable.

CCA-F Note:
  sanitize() is called BEFORE every API request in --safe mode.
  It is a best-effort control, not a security boundary: a determined
  attacker can always encode secrets or injection strings in ways that
  evade regex patterns.  The goal is to catch ACCIDENTAL leakage and
  OBVIOUS attacks while keeping false-positive rates low enough to be
  usable in production.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_TEXT_LENGTH = 10_000  # Truncate input to this many characters


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class SanitizeResult:
    """
    Return value of sanitize().

    Attributes:
        safe            True if the text passed all checks (or only triggered
                        warnings in non-strict mode).
        text            The cleaned/normalised text to use instead of the raw input.
        warnings        Non-fatal issues found (e.g. suspected prompt injection).
        blocked_reason  Non-empty when safe=False; explains why the text was blocked.
    """
    safe: bool
    text: str
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str = ""


# ── Pattern tables ─────────────────────────────────────────────────────────────

# Prompt-injection detection patterns (case-insensitive).
# These indicate an attempt to override the model's system instructions.
# Flagged as WARN normally, BLOCK in strict mode.
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|context)",
     "Prompt injection attempt: 'ignore previous instructions'"),
    (r"forget\s+(everything|all|all\s+previous|prior)",
     "Prompt injection attempt: 'forget everything'"),
    (r"act\s+as\s+(an?\s+)?(unrestricted|jailbreak|uncensored|unfiltered|DAN\b)",
     "Prompt injection attempt: 'act as unrestricted'"),
    (r"developer\s+mode\s*(enabled|on|activate)?",
     "Prompt injection attempt: 'developer mode'"),
    (r"<\s*system\s*>",
     "Prompt injection attempt: fake <system> tag"),
    (r"\[\s*system\s*\]",
     "Prompt injection attempt: fake [SYSTEM] tag"),
    (r"you\s+are\s+now\s+(a\s+)?(different|new|unrestricted|free)",
     "Prompt injection attempt: role reassignment"),
    (r"disregard\s+(your\s+)?(previous|prior|earlier|all)\s+(instructions?|training|guidelines?)",
     "Prompt injection attempt: 'disregard instructions'"),
]

# Secret / credential detection patterns.
# These are ALWAYS blocked regardless of strict mode because credentials
# must never be forwarded to any third-party API endpoint.
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Anthropic API key
    (r"sk-ant-[a-zA-Z0-9\-_]{20,}",
     "Secret detected: Anthropic API key (sk-ant-)"),
    # Generic sk- key (OpenAI-style, Stripe, etc.)
    (r"(?<![a-zA-Z0-9])sk-[a-zA-Z0-9]{20,}",
     "Secret detected: API key (sk-)"),
    # AWS access key
    (r"AKIA[A-Z0-9]{16}",
     "Secret detected: AWS access key (AKIA...)"),
    # PEM private key block
    (r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
     "Secret detected: PEM private key block"),
    # JWT: three base64url segments separated by dots
    (r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}",
     "Secret detected: JSON Web Token (JWT)"),
    # Inline password assignment
    (r"password\s*[=:]\s*\S{6,}",
     "Secret detected: inline password value"),
    # GitHub personal access token
    (r"ghp_[a-zA-Z0-9]{36}",
     "Secret detected: GitHub personal access token (ghp_)"),
    # Generic Bearer token
    (r"Bearer\s+[a-zA-Z0-9\-_\.]{20,}",
     "Secret detected: Bearer token"),
]

# Pre-compile for performance
_COMPILED_INJECTION = [
    (re.compile(pat, re.IGNORECASE | re.MULTILINE), desc)
    for pat, desc in _INJECTION_PATTERNS
]
_COMPILED_SECRETS = [
    (re.compile(pat, re.IGNORECASE | re.MULTILINE), desc)
    for pat, desc in _SECRET_PATTERNS
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _strip_control_characters(text: str) -> str:
    """
    Remove ASCII control characters except tab, newline, carriage return.
    Also normalises Unicode to NFC form and collapses zero-width characters.

    CCA-F Note: Control characters can be used to obscure injection strings
    from human review while still influencing model tokenisation.
    """
    # Unicode NFC normalisation (combines decomposed sequences)
    text = unicodedata.normalize("NFC", text)

    result = []
    for ch in text:
        cp = ord(ch)
        # Keep printable characters, tab (9), newline (10), carriage return (13)
        if cp in (9, 10, 13) or (32 <= cp < 127) or cp >= 160:
            cat = unicodedata.category(ch)
            # Drop zero-width / format control characters (category Cf)
            # but keep legitimate Unicode letters, punctuation, etc.
            if cat == "Cf" and cp not in (0x200B,):  # zero-width space is ok
                continue
            result.append(ch)
    return "".join(result)


def _normalise_whitespace(text: str) -> str:
    """
    Replace non-standard Unicode whitespace characters with regular spaces.

    CCA-F Note: Unicode contains ~25 different whitespace code points.
    Models tokenise them differently; normalising prevents tokenisation
    tricks that bypass injection pattern matching.
    """
    # Unicode "Separator, space" characters that aren't ordinary ASCII space
    _UNICODE_SPACES = (
        " ",  # non-breaking space
        " ",  # ogham space mark
        " ",  # en quad
        " ",  # em quad
        " ",  # en space
        " ",  # em space
        " ",  # three-per-em space
        " ",  # four-per-em space
        " ",  # six-per-em space
        " ",  # figure space
        " ",  # punctuation space
        " ",  # thin space
        " ",  # hair space
        " ",  # narrow no-break space
        " ",  # medium mathematical space
        "　",  # ideographic space
    )
    for ws in _UNICODE_SPACES:
        text = text.replace(ws, " ")
    return text


# ── Public API ─────────────────────────────────────────────────────────────────

def sanitize(text: str, strict: bool = False) -> SanitizeResult:
    """
    Sanitise a single string before sending it to the Claude API.

    Processing steps (in order):
      1. Truncate to MAX_TEXT_LENGTH characters
      2. Strip control characters
      3. Normalise Unicode whitespace
      4. Check for prompt-injection patterns (warn normally, block in strict)
      5. Check for secret/credential patterns (ALWAYS blocks)

    Args:
        text:   Raw user-supplied text.
        strict: If True, promote injection warnings to blocks.

    Returns:
        SanitizeResult with safe=True if the text may be forwarded to the API.

    CCA-F Note:
        Secrets always block because forwarding credentials to a third-party
        API endpoint is unacceptable regardless of user intent.
        Injection patterns only block in strict mode because the user might
        legitimately be studying or testing prompt-injection techniques.
    """
    warnings: list[str] = []

    # Step 1 — truncate
    if len(text) > MAX_TEXT_LENGTH:
        warnings.append(
            f"Input truncated from {len(text)} to {MAX_TEXT_LENGTH} characters"
        )
        text = text[:MAX_TEXT_LENGTH]

    # Step 2 — strip control characters
    text = _strip_control_characters(text)

    # Step 3 — normalise whitespace
    text = _normalise_whitespace(text)

    # Step 4 — prompt injection check
    for regex, description in _COMPILED_INJECTION:
        if regex.search(text):
            if strict:
                return SanitizeResult(
                    safe=False,
                    text=text,
                    warnings=warnings,
                    blocked_reason=description,
                )
            else:
                warnings.append(description)

    # Step 5 — secret / credential check (always blocks)
    for regex, description in _COMPILED_SECRETS:
        if regex.search(text):
            return SanitizeResult(
                safe=False,
                text=text,
                warnings=warnings,
                blocked_reason=description,
            )

    return SanitizeResult(safe=True, text=text, warnings=warnings)


def sanitize_messages(
    messages: list[dict],
    strict: bool = False,
) -> tuple[list[dict], list[SanitizeResult]]:
    """
    Apply sanitize() to every user-role message in a messages list.

    Only 'user' role messages are processed; 'assistant' messages and
    messages with non-string content (tool results, image blocks, etc.)
    are passed through unchanged.

    Args:
        messages: Anthropic-format messages list
                  [{"role": "user"|"assistant", "content": str | list}, ...]
        strict:   Passed through to sanitize().

    Returns:
        (clean_messages, results) where:
          clean_messages  is the updated list (safe messages have cleaned text)
          results         is one SanitizeResult per message (None for skipped)

    CCA-F Note:
        If ANY message is blocked the caller should abort the API request.
        Partial sanitisation (blocking one message, forwarding others) would
        still allow an adversary to inject via earlier turns.
    """
    clean_messages: list[dict] = []
    results: list[SanitizeResult] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Only sanitise string-content user messages
        if role == "user" and isinstance(content, str):
            result = sanitize(content, strict=strict)
            results.append(result)
            if result.safe:
                clean_messages.append({**msg, "content": result.text})
            else:
                # Return the blocked message unchanged so the caller can
                # report the exact text that triggered the block.
                clean_messages.append(msg)
        else:
            results.append(SanitizeResult(safe=True, text=str(content)))
            clean_messages.append(msg)

    return clean_messages, results
