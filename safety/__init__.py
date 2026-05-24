"""
safety/ — Phase 5: Safety & Guardrails

CCA-F Domain: Safety & Responsible Use
This package demonstrates production-grade safety patterns for Claude
API integrations. Four complementary modules, each targeting a
different layer of the request/response lifecycle:

  sanitizer.py  -- INPUT layer
                   Inspect user text before it reaches the API.
                   Blocks prompt injection attempts and accidental
                   secret leakage. Normalises whitespace and length.

  filter.py     -- OUTPUT layer
                   Redact PII and secrets from Claude's responses
                   before they reach the user or are saved to disk.
                   Regex-based best-effort — not a security boundary.

  budget.py     -- COST layer
                   Per-session USD cap and requests-per-minute limit.
                   Raises BudgetExceededError before the API call
                   so the spending boundary is always respected.

  audit.py      -- AUDIT layer
                   Append-only JSON-lines log of every safety event
                   (blocks, warns, redactions, budget limits, API calls).
                   Never raises — audit failures are silent.

Usage from main.py:
  from safety.sanitizer import sanitize, sanitize_messages
  from safety.filter    import filter_output, filter_tool_result
  from safety.budget    import BudgetEnforcer, BudgetExceededError
  from safety.audit     import Auditor, AuditEvent, get_auditor, set_auditor

CCA-F Note: Safety is a mindset, not a single control.
  These four modules guard different failure modes:
    sanitizer -> adversarial input (prompt injection, accidental secrets)
    filter    -> sensitive output leakage (PII, credentials in replies)
    budget    -> runaway cost from loops or abuse
    audit     -> forensics and compliance after the fact
"""
