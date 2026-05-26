# Changelog

All notable changes to this project will be documented in this file.

---

## [v0.1.0] — 2026-05-26

### What It Does

AdoptviaAI (`avai`) is a fully-featured Python CLI that demonstrates
production-style Claude API patterns across five implementation phases:

| Phase | Domain | What is demonstrated |
|-------|--------|----------------------|
| 1 | API Fundamentals, Prompt Engineering, Cost & Context Management | Messages API wrapper, prompt caching via `cache_control`, `CostTracker` with all four token types, `ConversationManager` with auto-summarise at 20 messages |
| 2 | Tool Use & Function Calling | `ToolRegistry`, AST-safe calculator (no `eval()`), `file_reader`, `save_note`, `get_project_info`, agentic tool loop in `ToolExecutor` |
| 3 | Agents & Orchestration | `BaseAgent`, `ResearchAgent`, `WriterAgent`, `OrchestratorAgent`, three reusable prompt chains |
| 4 | Model Context Protocol | `FastMCP` server over stdio, hand-rolled JSON-RPC client, `MCPBridge` for dynamic tool discovery |
| 5 | Safety & Responsible Use | Input sanitizer (injection + secret detection), output filter (PII redaction), `BudgetEnforcer` (USD cap + RPM limit), append-only audit log |

### Install

**Prerequisites:** Python 3.10+, an Anthropic API key.

```bash
# Clone and set up (macOS / Linux)
cd AdoptviaAI
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY=sk-ant-...
avai version
```

```powershell
# Windows PowerShell
cd AdoptviaAI
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
# Edit .env → set ANTHROPIC_API_KEY=sk-ant-...
avai version
```

### Demo Commands

```bash
# Verify the install and see phase / domain coverage
avai version

# Single-shot question with safety pipeline
avai ask --safe "What is prompt caching?"

# Interactive multi-turn chat
avai chat

# Run the AST-safe calculator tool directly (no API call)
avai tools run calculator expression="(100 + 50) * 2"

# Run a specialist research agent with an agentic tool loop
avai agent run researcher "what tools are available?"

# List tools and resources exposed by the MCP server
avai mcp tools

# Check budget status and audit log
avai safety status
avai safety audit
```

See [`docs/demo.md`](docs/demo.md) for full example terminal output for each command.

---

## Built With Claude

This project was built collaboratively with Claude (Anthropic) as an AI
development partner — not just as the API being demonstrated, but as an
active participant in the architecture, implementation, and debugging process.

This reflects a real-world AI-augmented development workflow:
- Claude API powers the CLI features being demonstrated
- Claude (claude.ai) was used throughout development for architecture
  decisions, code generation, debugging, and documentation
- Demonstrates practical ability to leverage Claude as both a tool and
  a development accelerator

---

## Known Limitations

- **MCP naming conflict:** the local `mcp/` package shadows the pip `mcp`
  package when imported from `main.py`. The server intentionally runs as a
  subprocess to work around this. In a real project you would rename the
  local package.
- **Safety is best-effort:** the input sanitizer catches obvious injection
  strings and accidental secret leakage; it is not a security boundary.
  Determined adversaries can encode payloads that evade regex patterns.
- **Prompt-cache TTL is 5 minutes:** Anthropic's ephemeral cache expires
  after five minutes of inactivity. Long gaps between messages will incur
  a cache-write charge on the next call.
- **No streaming:** all responses are returned as complete messages. Adding
  `stream=True` to the `messages.create` call is straightforward but was
  left out to keep the token-counting logic simple.
- **Windows console encoding:** UTF-8 output requires
  `Console(legacy_windows=False)` and stdout reconfiguration. Some older
  Windows terminal emulators may still render certain Unicode characters
  incorrectly.
- **Single-process rate limiting:** `BudgetEnforcer`'s RPM window is
  process-local. If you run multiple `avai` instances concurrently the
  per-process limits do not coordinate.
