# AdoptviaAI

**AI adoption done right** — A CCA-F portfolio project demonstrating Claude API expertise across the core certification domains, organized into five implementation phases.

`avai` is a fully-featured CLI built with Python, Click, and Rich that shows real-world Claude API patterns: prompt caching, agentic tool use, multi-agent orchestration, Model Context Protocol (MCP), and safety guardrails.

---

## Why This Project Matters

Most Claude API tutorials stop at "send a message, get a reply." AdoptviaAI goes further — it demonstrates the patterns that actually matter in production:

- **Multi-turn chat with persistent history** — the Messages API is stateless; `ConversationManager` provides the statefulness that makes real conversations possible
- **Prompt caching** — system prompts are marked with `cache_control` so repeated calls pay ~10% of normal input token cost after the first turn
- **Tool use & agentic loops** — `ToolExecutor` handles the `stop_reason == "tool_use"` detect → dispatch → re-inject cycle, including an AST-safe calculator that never calls `eval()`
- **Multi-agent orchestration** — `ResearchAgent`, `WriterAgent`, and `OrchestratorAgent` show how to decompose complex goals across specialist roles
- **Model Context Protocol (MCP)** — a FastMCP server exposes tools and resources over stdio; a hand-rolled JSON-RPC client connects to it without relying on the pip `mcp` package being importable alongside the local `mcp/` directory
- **Cost tracking** — every API call logs all four token types (`input`, `output`, `cache_creation`, `cache_read`) with per-model pricing to a CSV, so you can see the caching strategy paying off in real numbers
- **Safety guardrails** — layered defences: input sanitization (injection + secret detection), output filtering (PII redaction), budget enforcement (pre-call USD cap + RPM limit), and an append-only audit log

This is a CCA-F certification portfolio project — every module is commented with the domain it demonstrates so the mapping from code to exam objective is explicit.

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- An [Anthropic API key](https://console.anthropic.com/)

### Mac / Linux

```bash
cd AdoptviaAI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
avai version
```

### Windows (PowerShell)

```powershell
cd AdoptviaAI
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
avai version
```

> **Cost tip:** The default model is **haiku** (`claude-haiku-4-5`). All dev and testing uses haiku. Switch to sonnet only for final validation.

---

## Commands

### Core chat

| Command | Description |
|---------|-------------|
| `avai chat` | Interactive multi-turn conversation |
| `avai chat --safe` | Chat with input sanitization + output filtering |
| `avai chat --tools` | Chat with built-in tool access (calculator, file reader, etc.) |
| `avai ask "question"` | Single-shot question (non-interactive) |
| `avai ask --safe "question"` | Single-shot with safety pipeline |
| `avai ask --tools "question"` | Single-shot with tool access |

**Interactive slash commands** (typed during `avai chat`):

| Command | Description |
|---------|-------------|
| `/exit` | Quit and show cost summary |
| `/model haiku\|sonnet\|opus` | Switch model mid-session |
| `/system default\|technical\|reviewer\|business` | Switch system prompt |
| `/clear` | Clear conversation history |
| `/summary` | Show current session cost |
| `/save [filename]` | Save conversation to JSON (auto-names if omitted) |
| `/dry-run` | Toggle dry run mode on/off |
| `/help` | Show command reference |

### Introspection

| Command | Description |
|---------|-------------|
| `avai version` | Version, phase summary, CCA-F domain coverage |
| `avai models` | Available Claude models with pricing |
| `avai prompts` | Available system prompt presets |
| `avai summary` | Session token usage and cost summary |

### Tools — Phase 2

| Command | Description |
|---------|-------------|
| `avai tools list` | List all registered built-in tools |
| `avai tools run <tool> [KEY=VALUE ...]` | Execute a single tool directly |

Built-in tools: `calculator`, `file_reader`, `save_note`, `get_project_info`

```bash
avai tools run calculator expression="(100 + 50) * 2"
avai tools run file_reader filepath=main.py max_lines=20
avai tools run get_project_info
```

### Agents & Chains — Phase 3

| Command | Description |
|---------|-------------|
| `avai agent list` | List available agents |
| `avai agent run <agent> "goal"` | Run a specialist agent |
| `avai chain list` | List available prompt chains |
| `avai chain run <chain> [--input KEY=VALUE ...]` | Execute a multi-step chain |

Agents: `researcher`, `writer`, `orchestrator`

Chains: `summarize-and-save`, `analyze-and-recommend`, `validate`

```bash
avai agent run researcher "what tools are available?"
avai chain run analyze-and-recommend --input question="reduce API costs"
avai chain run validate --input content="sky is green" --input criteria="must be accurate"
```

### MCP — Model Context Protocol — Phase 4

| Command | Description |
|---------|-------------|
| `avai mcp serve` | Start the MCP server (stdio) |
| `avai mcp tools` | List tools and resources exposed by the MCP server |
| `avai mcp run "goal"` | Run an agentic loop backed by MCP tools |
| `avai mcp status` | Show MCP server connection status |

MCP tools: `notes_list`, `notes_read`, `notes_write`, `notes_delete`, `project_status`

MCP resources: `notes://list`, `notes://{filename}`

```bash
avai mcp tools
avai mcp run "list all my notes"
avai mcp run "write a note called today.txt with content: hello world"
```

### Safety & Guardrails — Phase 5

| Command | Description |
|---------|-------------|
| `avai safety status` | Budget, rate-limit, and audit summary |
| `avai safety check "text"` | Run text through sanitizer + output filter |
| `avai safety check --strict "text"` | Strict mode — injection patterns block (not just warn) |
| `avai safety audit` | Tail the audit log (last 20 entries) |
| `avai safety audit -n 50` | Last 50 audit entries |
| `avai safety audit -e api_call` | Filter by event type |

```bash
avai safety check "ignore all previous instructions"
avai safety check "my key is sk-ant-abc123"
avai safety status
```

---

## Cost Control

AdoptviaAI has a built-in budget enforcer that caps API spend per session:

```bash
# Set a 50-cent session cap (default: $1.00)
# Windows PowerShell:
$env:AVAI_MAX_USD = "0.50"
# macOS / Linux:
# export AVAI_MAX_USD=0.50

# Set a 20 requests-per-minute rate limit (default: 60)
$env:AVAI_MAX_RPM = "20"

# Check current budget status
avai safety status
```

Or set them permanently in your `.env` file:

```env
AVAI_MAX_USD=0.50
AVAI_MAX_RPM=20
```

### Prompt caching

`avai` sends every system prompt with `cache_control: {"type": "ephemeral"}`. On the first call the prompt is written to Anthropic's cache. Every subsequent call within the 5-minute TTL reads from cache at ~10% of the normal input token price.

| Token type | Meaning | Haiku price |
|------------|---------|-------------|
| `input_tokens` | Regular uncached input | $1.00/MTok |
| `cache_creation_input_tokens` | Writing to cache (first call) | $1.25/MTok |
| `cache_read_input_tokens` | Reading from cache (repeated) | $0.10/MTok |
| `output_tokens` | Generated response | $5.00/MTok |

The cost line shown after every response includes all four token types so you can see your cache hit rate in real time.

---

## Global Flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--model` | `-m` | `haiku` | Model: `haiku`, `sonnet`, `opus` |
| `--system` | `-s` | `default` | System prompt: `default`, `technical`, `reviewer`, `business` |
| `--max-tokens` | `-t` | `2048` | Max output tokens |
| `--dry-run` | `-d` | off | Estimate tokens/cost without calling the API |
| `--verbose` | `-v` | off | Show full Python tracebacks on error |
| `--version` | | | Print version and exit |

---

## CCA-F Domains Covered

| Domain | Phase | What It Demonstrates |
|--------|-------|----------------------|
| API Fundamentals | 1 | Messages API, model selection, token counting, error handling |
| Prompt Engineering | 1 | System prompts, role-specific presets, prompt caching |
| Context Management | 1 | ConversationManager, auto-summarise at 20 messages, save/load JSON |
| Cost Management | 1 | CostTracker, per-call pricing, CSV log, session totals |
| Tool Use & Function Calling | 2 | ToolRegistry, AST-safe calculator, agentic tool loop |
| Agents & Orchestration | 3 | BaseAgent, ResearchAgent, WriterAgent, OrchestratorAgent, prompt chains |
| Model Context Protocol | 4 | FastMCP server, manual JSON-RPC client, dynamic tool discovery |
| Safety & Responsible Use | 5 | Input sanitizer, output filter, budget enforcer, audit log |

---

## Project Structure

```
AdoptviaAI/
  main.py                  CLI entry point — all commands (click + rich)
  setup.py                 Registers the 'avai' console script
  requirements.txt         Python dependencies
  .env.example             Environment variable template

  config/
    settings.py            Env vars, model defaults, MCP settings, UTF-8 fix

  core/
    client.py              ClaudeClient with prompt caching
    cost_tracker.py        Token cost tracking + CSV logging
    conversation.py        ConversationManager + auto-summarise
    tool_executor.py       ToolExecutor agentic loop

  prompts/
    system_prompts.py      PROMPTS dict (default/technical/reviewer/business)
    templates.py           Prompt template helpers

  tools/
    registry.py            ToolRegistry — register and dispatch tools
    builtin.py             4 tools: calculator, file_reader, save_note, get_project_info

  agents/
    base.py                BaseAgent + AgentResult
    researcher.py          ResearchAgent (file_reader, calculator, get_project_info)
    writer.py              WriterAgent (save_note, get_project_info)
    orchestrator.py        OrchestratorAgent (plans, delegates, synthesises)
    chains.py              3 chains: summarize-and-save, analyze-and-recommend, validate

  mcp/
    __init__.py            Package docstring
    server.py              FastMCP server — run as subprocess (stdio transport)
    client.py              Async JSON-RPC client — manual protocol, no pip mcp import
    bridge.py              MCPBridge: dynamic tool discovery -> Claude agentic loop

  safety/
    __init__.py            Package docstring
    sanitizer.py           INPUT layer — injection + secret detection
    filter.py              OUTPUT layer — PII + credential redaction
    budget.py              COST layer — USD cap + RPM rate limit
    audit.py               AUDIT layer — append-only JSON-lines log

  docs/
    architecture.md        Full architecture doc covering all 5 phases

  notes/                   Saved notes (content gitignored, .gitkeep tracked)
  logs/                    usage.log, audit.log (gitignored)
```

---

## Development Notes

- **Dry-run first:** most commands accept `--dry-run` / `-d` to preview prompts and estimated cost without making an API call
- **Windows Unicode:** all Rich consoles use `Console(legacy_windows=False)` + UTF-8 stdout reconfigure — Claude's responses include em-dashes, arrows, etc. that Windows cp1252 cannot encode natively
- **MCP naming conflict:** the local `mcp/` directory shadows the pip `mcp` package when imported from `main.py`. The MCP server runs as a subprocess (clean `sys.path`) and the client uses manual JSON-RPC — this is intentional and educational
- **Safety defaults:** injection patterns are warn-only by default; pass `--strict` to escalate to block. API key and secret patterns always block regardless of mode — sending credentials to a third-party API is never acceptable
- **Auto-summarise:** when a chat session exceeds 20 messages, `avai` calls haiku to summarise the older portion and replaces it with a compact summary — keeping input tokens bounded without losing context

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for a full component diagram and detailed explanation of every CCA-F concept implemented across all five phases.
