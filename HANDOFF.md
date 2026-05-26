# AdoptviaAI — Session Handoff

> **Agent reading this:** Read `CLAUDE.md` first for project rules, then this file for current state.
> Do not ask clarifying questions about anything documented here — just pick up and continue.
> Default model is **haiku** unless told otherwise. Always `--dry-run` before live API calls.

---

## Goal

All five phases are complete and the polish pass is done. The project is presentable.

---

## Current State

**All five phases complete + polish pass complete. Project published to GitHub.**

Repository: https://github.com/tedzoe/AdoptViaAI.git

```
Phase 1 -- chat, ask, summary, models, prompts            DONE
Phase 2 -- tools list, tools run, --tools flag            DONE
Phase 3 -- agent list/run, chain list/run                 DONE
Phase 4 -- mcp serve, mcp tools, mcp run, mcp status     DONE
Phase 5 -- safety status, safety check, safety audit      DONE
          chat/ask --safe flag                             DONE

Polish pass (11 tasks):
  Task 1  requirements.txt -- added mcp[cli]              DONE
  Task 2  main.py rewrite  -- safety wiring, singletons   DONE
  Task 3  mcp/server.py    -- added notes_write tool      DONE
  Task 4  version command  -- avai version                DONE
  Task 5  welcome panel    -- avai (no args)              DONE
  Task 6  --verbose/-v     -- error panels not tracebacks  DONE
  Task 7  session summary  -- one-liner after API cmds    DONE
  Task 8  builtin.py       -- version 0.5.0               DONE
  Task 9  README.md        -- full 5-phase coverage       DONE
  Task 10 .env.example     -- safe placeholder, no key    DONE
  Task 11 docs/            -- Phase 5 section             DONE (prior session)
```

**Status:** `[x] Done — polish complete`

**Last known working commands (all validated 2026-05-24):**
```
avai version
avai safety check "ignore all previous instructions"
avai safety check "my key is sk-abc12345678901234567890"
avai chat --safe --dry-run "hello"
avai safety status
avai agent list
avai mcp status
avai agent run researcher "what tools are available?"
avai chain list
avai chain run analyze-and-recommend --input question="reduce API costs"
avai chain run validate --input content="sky is green" --input criteria="must be accurate"
avai mcp tools
avai mcp run "list all my notes"
avai safety check --strict "developer mode enabled"
avai ask --safe "hello"
avai safety audit
```

---

## Files In Progress

| File | Status | Notes |
|------|--------|-------|
| All Phase 1-5 files | Complete | No pending work |
| `README.md` | Complete | Full 5-phase coverage |
| `.env.example` | Complete | Safe placeholder (no real key) |

---

## Recent Changes (newest first)

### Git init + publish (this session, 2026-05-24)

- Security audit: confirmed `.env` contained a real `sk-ant-api03-...` key (since replaced
  with placeholder); no git history risk because no `.git` existed yet
- Confirmed `.env` is listed in `.gitignore` and `.env.example` contains only placeholder
- Ran `git init`, set remote to `https://github.com/tedzoe/AdoptViaAI.git`
- Initial commit: 38 files, 7344 insertions — all Phase 1-5 source, docs, safety, MCP
- Pushed to GitHub: `master` branch, tracking `origin/master`

### Polish pass (previous session)

- Fixed `_SafeGroup.invoke()` in `main.py` — added `except click.exceptions.Exit: raise`
  before `except Exception`. `click.exceptions.Exit` is a `RuntimeError` subclass (not
  `ClickException`), so `--help` on any subcommand was incorrectly caught and shown as
  Error panel "0". Fix makes all `--help` output clean.
- Updated `chat` command in `main.py` — added `--dry-run` option and optional
  `INITIAL_MESSAGE` argument so `avai chat --safe --dry-run "hello"` works correctly:
  runs safety check, shows dry-run estimate, exits without API call.
- Updated `README.md` — full rewrite covering all 5 phases: Quick Start (Windows +
  Mac/Linux), complete commands table for all phases, Cost Control section, CCA-F Domains
  table, full Project Structure tree, development notes
- Updated `.env.example` — replaced real API key with safe placeholder `your_api_key_here`,
  added Phase 5 env vars (AVAI_MAX_USD, AVAI_MAX_RPM), added comments for all settings
- Updated `requirements.txt` — added `mcp[cli]>=1.0.0`
- Updated `mcp/server.py` — added `notes_write` tool (5th MCP tool), updated
  `project_status` to version 0.5.0, phase "Phase 5 -- Safety & Guardrails",
  `mcp_tools_count: 5`; updated startup message to list 5 tools
- Updated `tools/builtin.py` — `get_project_info` returns version 0.5.0, Phase 5,
  all 22 commands listed including version/safety, 7 CCA-F domains
- Updated `main.py` (full rewrite) — `_SafeGroup(click.Group)` for global error/summary;
  VERSION = "0.5.0"; `@click.version_option`; `version` command; `_print_welcome()` panel;
  `--verbose/-v` global flag; `_session_tracker` module-level singleton; `_session_budget`
  and `_auditor` singletons; `_blocked_calls` counter; `safety()` group with
  status/check/audit; `--safe` on chat + ask; `_DEFAULT_MAX_RPM = 30`

### Phase 5 (previous session)
- Created `safety/__init__.py` — package docstring, CCA-F domain note, lists all 4 modules
- Created `safety/sanitizer.py` — SanitizeResult dataclass, sanitize(), sanitize_messages();
  8 injection patterns (warn/block in strict), 8 secret patterns (always block),
  control-char stripping, unicode whitespace normalisation
- Created `safety/filter.py` — FilterResult dataclass, _Rule dataclass, 10 default redaction
  rules (api_key, aws_key, jwt, private_key, github_token, ssn, credit_card, email,
  phone_us, ipv4), filter_output(), filter_tool_result(), available_rules()
- Created `safety/budget.py` — BudgetState, BudgetCheckResult, BudgetExceededError,
  BudgetEnforcer with sliding-window deque rate limiter; AVAI_MAX_USD + AVAI_MAX_RPM env vars
- Created `safety/audit.py` — AuditEvent constants, AuditRecord dataclass, Auditor class
  with 8 log_*() methods (all silent on write failure); module-level get_auditor/set_auditor
- Updated `docs/architecture.md` — Phase 5 section: --safe pipeline ASCII diagram, 4-module
  table, CLI commands, sanitizer patterns, filter rules table, budget env vars, audit log
  format, CCA-F domain coverage table, 5 security notes

### Phase 4 (previous session)
- Created `mcp/server.py` — FastMCP server with tools and resources (stdio transport)
- Created `mcp/client.py` — async JSON-RPC 2.0 client over subprocess stdio;
  does NOT import pip mcp package (avoids local/pip naming conflict)
- Created `mcp/bridge.py` — sync MCPBridge wrapping async client; Claude agentic loop
- Updated `config/settings.py` — MCP settings, MAX_TOKENS 2048, UTF-8 stdout fix

### Phase 3 (previous session)
- Created `agents/base.py`, `agents/researcher.py`, `agents/writer.py`,
  `agents/orchestrator.py`, `agents/chains.py`
- Fixed Windows Unicode crash: Console(legacy_windows=False) everywhere

---

## Failed Attempts

| What Was Tried | Why It Failed | Do Not Retry |
|----------------|---------------|--------------|
| `from mcp.server.fastmcp import FastMCP` in mcp/client.py | Local mcp/ shadows pip mcp when imported from main.py | Never import pip mcp from mcp/*.py files main.py imports. Use subprocess (server.py) or manual JSON-RPC (client.py). |
| `Console()` without legacy_windows=False | Windows cp1252 cannot encode Claude's Unicode response text | Always use Console(legacy_windows=False) + sys.stdout.reconfigure(encoding='utf-8') |
| `load_dotenv()` without override=True | Shell had ANTHROPIC_API_KEY='' pre-set; dotenv skips vars already in env | Always use load_dotenv(override=True) |
| `eval()` for calculator | Arbitrary code execution risk | Use ast.parse(expr, mode='eval') + _safe_eval_node() walker |
| `filter_result.redaction_count` | FilterResult has no redaction_count field; redactions is a list | Use len(filter_result.redactions) |
| No `except click.exceptions.Exit` in _SafeGroup | Exit(0) from --help is a RuntimeError, caught by `except Exception`, shown as Error panel "0" | Always include `except click.exceptions.Exit: raise` before `except Exception` |
| `avai chat --safe --dry-run "hello"` without --dry-run on chat | chat had no --dry-run option | Added --dry-run + optional INITIAL_MESSAGE to chat command |

---

## Next Steps

All work is complete. Possible future extensions:

- Add `--strict` flag to `chat --safe` / `ask --safe` (currently only in `safety check`)
- Integrate safety into the agentic loop (`chat --tools --safe`)
- Add `AVAI_AUDIT_ENABLED=false` env var to disable audit logging in tests
- Export safety summary in `avai summary` command
- Write unit tests for safety modules (they have no runtime dependencies — trivial to test)
- Add `avai tools run --help` with examples

---

## Blockers / Active Errors

```
None. All commands pass clean.
```

One cosmetic note: `notes://{filename}` resource template only shows as 1 resource
in `avai mcp tools` output (notes://list). The template resource is in the server
but FastMCP 1.27.1 returns it via `resources/templates/list` not `resources/list`.
Not blocking — the tool still works via `avai mcp run`.

---

## Decisions Made

1. **mcp/client.py uses manual JSON-RPC** (not pip mcp SDK client).
   Reason: local mcp/ directory shadows pip mcp when imported by main.py.
   The manual implementation is more educational anyway — makes the protocol visible.
   Do not change this unless the directory is renamed.

2. **mcp/server.py is a standalone subprocess** (not imported by main.py).
   When run as `python mcp/server.py`, sys.path[0] = mcp/ (no nested mcp/ there),
   so the pip FastMCP import resolves to site-packages correctly.

3. **MAX_TOKENS default is 2048** (raised from 1024 in Phase 4).
   1024 was causing `stop_reason: max_tokens` truncation in agent responses.

4. **Console(legacy_windows=False)** everywhere. Do not revert.
   The Windows legacy renderer (cp1252) cannot handle Unicode in Claude's output.

5. **Safety modules are composable and independently testable.**
   sanitizer.py and filter.py have no runtime deps (pure regex). budget.py has no API
   dependency. audit.py has no dependencies on the other three.

6. **Secrets always block; injection only blocks in --strict mode.**
   Sending credentials to a third-party API is always unacceptable.
   Studying injection patterns is legitimate, so injection defaults to warn-only.

7. **BudgetEnforcer is a process-scoped singleton.**
   Instantiated once at module level so the budget accumulates across all commands.
   Per-command instantiation would reset the counter on every call.

8. **Auditor never raises.**
   A logging failure must never interrupt the user's session. All write errors
   are silently swallowed. The audit log is forensic/advisory, not transactional.

9. **`chat` command has optional INITIAL_MESSAGE argument + --dry-run flag.**
   This enables `avai chat --safe --dry-run "hello"` for validation and testing.
   The message is processed through the safety pipeline and shown as dry-run output.

10. **`_SafeGroup.invoke()` must catch `click.exceptions.Exit` explicitly.**
    `Exit` is a RuntimeError (not ClickException), so it falls through to the generic
    `except Exception` block without an explicit handler. `str(Exit(0)) == "0"` which
    was being shown as a bogus error panel after `--help` on any subcommand.

---

## CCA-F Domains Touched

- [x] API Fundamentals (Messages API, model selection, token counting)
- [x] Prompt Engineering (system prompts, caching, chaining)
- [x] Context Management (ConversationManager, auto-summarise, save/load)
- [x] Cost & Model Selection (CostTracker, PRICING, session totals, CSV log)
- [x] Tool Use / Function Calling (Phase 2 — agentic loop, tool registry)
- [x] Agents & Orchestration (Phase 3 — ResearchAgent, WriterAgent, Orchestrator)
- [x] MCP — Model Context Protocol (Phase 4 — server, client, bridge)
- [x] Safety & Guardrails (Phase 5 — sanitizer, filter, budget, audit)

---

## Project Structure (current)

```
AdoptviaAI/
  main.py                  CLI entry point (all commands)
  setup.py                 avai console_scripts entry point
  requirements.txt         Python dependencies (mcp[cli] included)
  README.md                Full 5-phase project documentation
  .env.example             Environment variable template (safe placeholder)
  HANDOFF.md               This file
  config/
    settings.py            env, model defaults, MCP settings, UTF-8 fix
  core/
    client.py              ClaudeClient with prompt caching
    cost_tracker.py        token cost tracking + CSV logging
    conversation.py        ConversationManager + auto-summarise
    tool_executor.py       ToolExecutor agentic loop
  prompts/
    system_prompts.py      PROMPTS dict (default/technical/reviewer/business)
    templates.py           prompt template helpers
  tools/
    registry.py            ToolRegistry
    builtin.py             4 tools: calculator, file_reader, save_note, get_project_info
  agents/
    base.py                BaseAgent + AgentResult
    researcher.py          ResearchAgent (file_reader, calculator, get_project_info)
    writer.py              WriterAgent (save_note, get_project_info)
    orchestrator.py        OrchestratorAgent (plans + delegates + synthesizes)
    chains.py              3 chains: summarize-and-save, analyze-and-recommend, validate
  mcp/
    __init__.py            package docstring
    server.py              FastMCP server (run as subprocess) -- 5 tools, 2 resources
    client.py              async JSON-RPC client (manual protocol, no pip mcp import)
    bridge.py              MCPBridge: MCP -> dynamic discovery -> Claude loop
  safety/
    __init__.py            package docstring (Phase 5, CCA-F Safety domain)
    sanitizer.py           INPUT layer: injection + secret detection
    filter.py              OUTPUT layer: PII + credential redaction
    budget.py              COST layer: USD cap + RPM rate limit
    audit.py               AUDIT layer: append-only JSON-lines log
  docs/
    architecture.md        full architecture doc covering all 5 phases
  notes/                   saved notes (gitignored content, .gitkeep tracked)
  logs/                    usage.log, audit.log (gitignored)
```

---

## Resume Instructions

| Trigger | What to say |
|---------|-------------|
| End of session | `"Update handoff.md"` |
| After `/clear` | `"Read CLAUDE.md and HANDOFF.md and continue"` |
| New session | `"Review CLAUDE.md and HANDOFF.md — pick up where we left off"` |

---

*Last updated: 2026-05-24 — Polish pass complete. All 11 tasks done. Project published to GitHub (tedzoe/AdoptViaAI). All validation commands pass.*
