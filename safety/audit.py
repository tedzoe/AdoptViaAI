"""
safety/audit.py — Phase 5: Audit Logging

CCA-F Domain: Safety & Responsible Use
This module guards the AUDIT layer — every significant safety event is
appended to logs/audit.log as a JSON-lines record for forensics and
compliance review.

Design principles:
  - Append-only: records are never modified or deleted by this module.
  - Never raises: a logging failure must not interrupt the user's session.
    All write errors are silently swallowed (the audit log is advisory,
    not transactional).
  - Session correlation: every record carries a session_id so related
    events can be grouped across a single CLI invocation.
  - Structured: JSON-lines format is both human-readable and
    machine-parseable for log aggregation tools.

Event taxonomy (AuditEvent constants):
  api_call        -- A Claude API request was made (model, tokens, cost)
  sanitize_block  -- sanitize() blocked an input
  sanitize_warn   -- sanitize() flagged a warning but allowed the input
  filter_redact   -- filter_output() redacted content from a response
  budget_block    -- BudgetEnforcer blocked a call (limit exceeded)
  budget_warn     -- BudgetEnforcer crossed the warning threshold
  session_start   -- Session began (CLI invocation)
  session_end     -- Session ended normally

CCA-F Note:
  This module has zero dependencies on the other safety modules so it
  can be used standalone.  Import order: always import audit last to
  avoid circular dependencies.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Event constants ────────────────────────────────────────────────────────────

class AuditEvent:
    """
    String constants for audit event types.

    CCA-F Note:
        Using constants (rather than an Enum) keeps the values directly
        usable as dict keys and JSON strings without .value unwrapping.
    """
    api_call       = "api_call"
    sanitize_block = "sanitize_block"
    sanitize_warn  = "sanitize_warn"
    filter_redact  = "filter_redact"
    budget_block   = "budget_block"
    budget_warn    = "budget_warn"
    session_start  = "session_start"
    session_end    = "session_end"


# ── Record type ────────────────────────────────────────────────────────────────

@dataclass
class AuditRecord:
    """
    A single audit log entry.

    All fields are JSON-serialisable.  Fields not applicable to a given
    event type are left as None / empty defaults.
    """
    # Always populated
    timestamp:   str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event:       str   = ""
    session_id:  str   = ""

    # api_call fields
    model:         Optional[str]   = None
    input_tokens:  Optional[int]   = None
    output_tokens: Optional[int]   = None
    cost_usd:      Optional[float] = None

    # sanitize_block / sanitize_warn fields
    blocked_reason: Optional[str]       = None
    warnings:       Optional[list[str]] = None

    # filter_redact fields
    redaction_count: Optional[int]       = None
    redaction_labels: Optional[list[str]] = None

    # budget_block / budget_warn fields
    budget_reason:  Optional[str]   = None
    total_usd:      Optional[float] = None
    remaining_usd:  Optional[float] = None

    # General-purpose metadata dict (for future extensibility)
    meta: Optional[dict[str, Any]] = None


# ── Auditor class ──────────────────────────────────────────────────────────────

class Auditor:
    """
    Append-only audit logger for safety events.

    Every log_*() method appends one JSON-lines record to the audit log
    file and returns immediately.  If the write fails for any reason (disk
    full, permission error, etc.) the exception is silently swallowed so
    the application can continue.

    Usage:
        auditor = Auditor(session_id="abc123", log_path="logs/audit.log")
        auditor.log_session_start()

        # ... application code ...

        auditor.log_api_call(model="haiku", input_tokens=100,
                             output_tokens=200, cost_usd=0.0002)
        auditor.log_session_end()

    CCA-F Note:
        One Auditor instance per session is the correct pattern.
        The module-level get_auditor() / set_auditor() functions let you
        share the same instance across multiple modules without passing
        it as a parameter everywhere.
    """

    def __init__(
        self,
        session_id: str,
        log_path:   str | Path,
        enabled:    bool = True,
    ) -> None:
        """
        Args:
            session_id: Unique identifier for this session (shown in every record).
            log_path:   Path to the audit log file (created if absent; appended to).
            enabled:    Set False to disable all logging (useful in tests / dry-run).
        """
        self._session_id = session_id
        self._log_path   = Path(log_path)
        self._enabled    = enabled

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _write(self, record: AuditRecord) -> None:
        """
        Append record as a JSON line to the audit log.

        Never raises.  Any exception is silently swallowed.

        CCA-F Note:
            We open in append mode ('a') so concurrent writes from multiple
            processes are safe on most file systems (each write is atomic
            at the OS level for lines shorter than PIPE_BUF).
        """
        if not self._enabled:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(record), ensure_ascii=False, default=str)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            # Audit failures must never interrupt the user session.
            pass

    def _base(self, event: str) -> AuditRecord:
        return AuditRecord(
            event=event,
            session_id=self._session_id,
        )

    # ── Log methods ────────────────────────────────────────────────────────────

    def log_session_start(self, meta: dict[str, Any] | None = None) -> None:
        """Log that a new session has started."""
        rec = self._base(AuditEvent.session_start)
        rec.meta = meta
        self._write(rec)

    def log_session_end(
        self,
        total_usd:   float = 0.0,
        total_calls: int   = 0,
    ) -> None:
        """Log that the session ended normally."""
        rec = self._base(AuditEvent.session_end)
        rec.total_usd = total_usd
        rec.meta = {"total_calls": total_calls}
        self._write(rec)

    def log_api_call(
        self,
        model:         str,
        input_tokens:  int,
        output_tokens: int,
        cost_usd:      float,
    ) -> None:
        """
        Log a completed API call.

        CCA-F Note:
            Call this AFTER the API responds so we have actual token counts.
        """
        rec = self._base(AuditEvent.api_call)
        rec.model         = model
        rec.input_tokens  = input_tokens
        rec.output_tokens = output_tokens
        rec.cost_usd      = round(cost_usd, 8)
        self._write(rec)

    def log_sanitize_block(
        self,
        blocked_reason: str,
        warnings:       list[str] | None = None,
    ) -> None:
        """Log that sanitize() blocked an input."""
        rec = self._base(AuditEvent.sanitize_block)
        rec.blocked_reason = blocked_reason
        rec.warnings       = warnings or []
        self._write(rec)

    def log_sanitize_warn(
        self,
        warnings: list[str],
    ) -> None:
        """Log that sanitize() issued warnings but allowed the input through."""
        rec = self._base(AuditEvent.sanitize_warn)
        rec.warnings = warnings
        self._write(rec)

    def log_filter_redact(
        self,
        redactions: list[tuple[str, str]],
    ) -> None:
        """
        Log that filter_output() redacted content.

        Args:
            redactions: List of (label, matched_text) from FilterResult.redactions.
                        The matched_text is NOT written to the audit log to
                        avoid persisting the sensitive value.
        """
        rec = self._base(AuditEvent.filter_redact)
        rec.redaction_count  = len(redactions)
        rec.redaction_labels = [label for label, _ in redactions]
        self._write(rec)

    def log_budget_block(
        self,
        reason:        str,
        total_usd:     float,
        remaining_usd: float,
    ) -> None:
        """Log that BudgetEnforcer blocked an API call."""
        rec = self._base(AuditEvent.budget_block)
        rec.budget_reason  = reason
        rec.total_usd      = round(total_usd, 6)
        rec.remaining_usd  = round(remaining_usd, 6)
        self._write(rec)

    def log_budget_warn(
        self,
        total_usd: float,
        max_usd:   float,
    ) -> None:
        """Log that the warning threshold was crossed."""
        rec = self._base(AuditEvent.budget_warn)
        rec.total_usd     = round(total_usd, 6)
        rec.remaining_usd = round(max_usd - total_usd, 6)
        rec.meta          = {"max_usd": max_usd}
        self._write(rec)


# ── Module-level singleton ─────────────────────────────────────────────────────
# A process-scoped default auditor, initialised with a fresh UUID each time.
# Replaced by set_auditor() once main.py has a real session ID.

_DEFAULT_LOG_PATH = Path(__file__).parent.parent / "logs" / "audit.log"

_global_auditor: Auditor = Auditor(
    session_id=str(uuid.uuid4()),
    log_path=_DEFAULT_LOG_PATH,
    enabled=True,
)


def get_auditor() -> Auditor:
    """
    Return the current process-scoped Auditor instance.

    CCA-F Note:
        Modules that need to log audit events should call get_auditor()
        rather than creating their own instance so all events share a
        single session_id.
    """
    return _global_auditor


def set_auditor(auditor: Auditor) -> None:
    """
    Replace the process-scoped Auditor instance.

    Call this at application startup (e.g. in main.py cli()) once you
    have generated the session ID so all subsequent get_auditor() calls
    return the correctly-initialised instance.
    """
    global _global_auditor
    _global_auditor = auditor


def new_session_id() -> str:
    """Generate a fresh session ID (short UUID4, first 8 hex chars)."""
    return uuid.uuid4().hex[:8]
