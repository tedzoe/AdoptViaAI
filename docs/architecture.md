# AdoptviaAI -- Architecture

> AI adoption done right

---

## System Architecture Overview

```
+-------------------------------------------------------------------------+
|                          avai CLI  (main.py)                            |
|                                                                         |
|  Phase 1: chat | ask | summary | models | prompts                      |
|  Phase 2: tools list | tools run                                        |
|  Phase 3: agent list | agent run | chain list | chain run               |
|  Phase 4: mcp serve | mcp tools | mcp run | mcp status                  |
|  Global flags: --model  --system  --max-tokens  --dry-run               |
+--+---------------------------------------------------+-------------------+
   |                                                   |
   v                                                   v
+----------------------+                  +-------------------------------+
|   config/            |                  |  prompts/                     |
|   settings.py        |                  |  system_prompts.py            |
|                      |                  |  templates.py                 |
|   .env loader        |                  |                               |
|   API key check      |                  |  PROMPTS dict                 |
|   Defaults           |                  |  TEMPLATES dict               |
|   MCP settings       |                  |                               |
+----------------------+                  +-------------------------------+
                      |
                      v
+-------------------------------------------------------------------------+
|                          core/                                          |
|                                                                         |
|  client.py              conversation.py    cost_tracker.py             |
|  ClaudeClient           ConversationMgr    CostTracker                  |
|  send_message()         add_message()      add_call()                   |
|   + cache_control       replace_history()  log_tool_call()              |
|   + tools param         summarize_if_      display_summary()            |
|                         needed()           CSV + [TOOL] logging         |
|                                                                         |
|  tool_executor.py (Phase 2)                                             |
|  ToolExecutor -- agentic loop                                           |
+-------------------------------------------------------------------------+
```

---

## Phase 1: API Fundamentals

### Prompt Caching
```python
system = [{"type": "text", "text": system_prompt,
           "cache_control": {"type": "ephemeral"}}]
```
- First call: `cache_creation_input_tokens` billed at ~1.25x input rate
- Subsequent calls (5-min TTL): `cache_read_input_tokens` at ~0.1x rate
- Net effect: 10x saving on repeated calls within a session

### Cost Management
Four token types x four per-model prices = actual cost per call.
Session totals displayed in Rich table. Every API call logged to
`logs/usage.log` (CSV).

### Context Management
Stateless API -> stateful app via messages list passed on every call.
Auto-summarisation at 20 messages. Save/load conversation JSON.

---

## Phase 2: Tool Use & Function Calling

### The Agentic Loop

```
User message
      |
      v
messages.create(tools=definitions)   <- iteration 1
      |
      +-- stop_reason == "end_turn"
      |         +--> return response text   done
      |
      +-- stop_reason == "tool_use"
               |
               +--> extract ToolUseBlock(s)
               +--> append as assistant message
               +--> call handler(inputs) locally
               +--> append tool_result as user message
               +--> messages.create()        <- iteration 2
                         |
                         +--> (loop until end_turn or max_iterations)
```

### Security
- calculator: AST-based safe eval (no eval())
- save_note: Path(filename).name strips path components + resolve() check
- file_reader: read-only, no write code path

---

## Phase 3: Agents & Multi-Step Reasoning

```
                    OrchestratorAgent
                     /               \
            ResearchAgent         WriterAgent
                 |                     |
          file_reader               save_note
          calculator            get_project_info
          get_project_info

            Prompt Chains (deterministic, you control steps)
          chain_summarize_and_save    (linear: A -> B -> save)
          chain_analyze_and_recommend (refinement: analyse -> options -> rank)
          chain_validate              (conditional: check -> fix? -> verdict)
```

### Agents vs Chains

| Pattern       | Who controls the sequence? | When to use               |
|---------------|---------------------------|---------------------------|
| Agentic loop  | Claude (autonomous)        | Unknown number of steps   |
| Prompt chain  | You (deterministic)        | Known sequence, lower cost|
| Orchestrator  | Claude + you (meta-plan)   | Multi-agent coordination  |

### Human-in-the-Loop
`avai agent run orchestrator --confirm "..."` shows the plan and pauses
before execution. Claude cannot take action until the user types "y".
This is critical for irreversible or costly operations.

---

## Phase 4: MCP (Model Context Protocol)

### What is MCP?

MCP is a standard protocol for connecting AI models to tool providers.
Instead of hardcoding tool definitions in your application (Phase 2),
you define tools in an MCP server. Any MCP-compatible client can
discover and use those tools at runtime.

### System Diagram

```
Claude API (Anthropic)
      ^
      | Anthropic tool format (input_schema)
      |
MCPBridge  (mcp/bridge.py)
      |
      | calls via MCPClient
      |
MCPClient  (mcp/client.py)
      |
      | JSON-RPC 2.0 over stdin/stdout
      | (subprocess stdio transport)
      v
MCP Server  (mcp/server.py)  <-- FastMCP, runs as child process
      |
      +-- Tool: notes_list      --> notes/ directory listing
      +-- Tool: notes_read      --> notes/<filename> content
      +-- Tool: notes_delete    --> notes/<filename> delete
      +-- Tool: project_status  --> version/phase metadata
      |
      +-- Resource: notes://list         --> directory as text
      +-- Resource: notes://{filename}   --> file content by URI
```

### MCP vs Hardcoded Tools (Phase 2 vs Phase 4)

| Aspect           | Phase 2 (hardcoded)          | Phase 4 (MCP)                |
|------------------|------------------------------|------------------------------|
| Tool definitions | Written in Python at dev time | Fetched from server (runtime)|
| Adding a tool    | Edit builtin.py, re-deploy   | Add @mcp_app.tool() only     |
| Tool provider    | Same process as client       | Separate subprocess (or net) |
| Multiple clients | Must duplicate tool code     | Any MCP client works         |
| Claude Desktop   | Not directly compatible      | Drop-in compatible           |

### Tools vs Resources: When to Use Each

**Tools** (callable, can have side effects):
- Use for CRUD operations: create, read, update, delete
- Take structured JSON arguments; return structured results
- Claude invokes them as function calls
- Examples: notes_read, notes_delete, project_status

**Resources** (addressable data, read-only):
- Use for data that clients browse or display
- Addressed by URI (notes://list, notes://math_test.txt)
- Support subscriptions for live change notifications
- Examples: notes://list, notes://{filename}

Rule of thumb: if it mutates state -> tool. If it's data retrieval -> resource.

### stdio Transport vs HTTP Transport

```
stdio transport (what avai uses):
  avai mcp run ---> spawns python mcp/server.py as subprocess
                        |
                    stdin/stdout
                        |
                    JSON-RPC 2.0
  
  + Zero network config
  + Perfect for local tools
  + Used by Claude Desktop
  - One client per server instance

HTTP/SSE transport (for remote servers):
  client ---> HTTP POST/GET ---> remote server (port 8080)
  
  + Multiple simultaneous clients
  + Works across machines
  + Supports auth (JWT, API keys)
  - Requires network setup
```

### Security: MCP Servers Run With Your Permissions

An MCP server process inherits the file system access and environment
variables of the user who starts it. Before running any MCP server:

1. Review the server source code
2. Check what tools it exposes and what they can do
3. Use least-privilege accounts in production
4. Never run untrusted MCP servers from unknown sources

AdoptviaAI's MCP server is restricted to notes/ by design
(path traversal prevention: Path(filename).name strips all dir
components, resolve() check verifies the path stays inside notes/).

### The Naming Conflict: Local mcp/ vs pip mcp Package

AdoptviaAI has a local mcp/ package AND uses the pip mcp SDK.
Python's sys.path would normally shadow one with the other.

Solution used here:
- mcp/server.py runs as a SUBPROCESS. Python sets sys.path[0] to
  the script's directory (mcp/). There is no nested mcp/ inside
  that directory, so the pip package resolves correctly.
- mcp/client.py implements the MCP JSON-RPC protocol manually
  (asyncio subprocess + JSON-RPC 2.0) without importing the pip SDK.
  This avoids the shadowing entirely AND is more educational.

---

---

## Phase 5: Safety & Guardrails

### The --safe Pipeline

Every message processed with `--safe` passes through four layers in sequence:

```
User input text
      |
      v
+---------------------+
| sanitizer.py        |  INPUT layer
| - strip controls    |
| - check injection   |  --> BLOCK (always) if secret detected
| - check secrets     |  --> BLOCK (strict) or WARN if injection detected
+---------------------+
      | safe text
      v
+---------------------+
| budget.py           |  COST layer
| - check USD cap     |  --> BudgetExceededError if over limit
| - check RPM limit   |  --> BudgetExceededError if rate exceeded
+---------------------+
      | allowed
      v
+---------------------+
| Claude API          |  (the actual API call)
+---------------------+
      | response text
      v
+---------------------+
| filter.py           |  OUTPUT layer
| - redact PII        |
| - redact secrets    |  --> replaces matches with [LABEL_REDACTED]
+---------------------+
      | filtered text
      v
+---------------------+
| audit.py            |  AUDIT layer (runs in parallel with all steps)
| - log api_call      |  --> appends JSON line to logs/audit.log
| - log blocks/warns  |  --> never raises, never interrupts
+---------------------+
      | displayed to user
```

### The Four Safety Modules

| Module        | Layer  | Activation               | What it does                                              |
|---------------|--------|--------------------------|-----------------------------------------------------------|
| sanitizer.py  | INPUT  | Before every API call    | Truncates, strips control chars, checks injection/secrets |
| filter.py     | OUTPUT | After every API response | Redacts PII and credentials from response text            |
| budget.py     | COST   | Before every API call    | Enforces per-session USD cap and RPM rate limit           |
| audit.py      | AUDIT  | All safety events        | Appends JSON-lines records to logs/audit.log              |

### CLI Commands (Phase 5)

```
avai chat --safe "your message"
    Activates all four safety layers for the chat session.
    Sanitizer + budget run before the API call.
    Filter + audit run after the API response.

avai ask --safe "your question"
    Same pipeline for a single-shot question.

avai safety status
    Shows session budget state and lists all active safety modules.
    No API call.

avai safety check "some text"
    Runs sanitizer and filter on the text and shows results.
    No API call. Add --strict to block on injection warnings.

avai safety audit
    Tails logs/audit.log as a Rich table.
    Options: --lines N (default 20), --event <type>
    Event types: api_call | sanitize_block | sanitize_warn |
                 filter_redact | budget_block | budget_warn |
                 session_start | session_end
```

### Sanitizer: Injection & Secret Patterns

**Prompt injection** (warn by default, block with `--strict`):
- "ignore previous/prior instructions"
- "forget everything / all previous"
- "act as unrestricted / jailbreak / DAN"
- "developer mode enabled"
- Fake `<system>` or `[SYSTEM]` tags
- "you are now a different / unrestricted" role reassignment
- "disregard your previous instructions / training"

**Secrets** (always block, regardless of `--strict`):
- `sk-ant-...` Anthropic API keys
- `sk-...` generic API keys (OpenAI, Stripe-style)
- `AKIA...` AWS access keys
- `-----BEGIN ... PRIVATE KEY-----` PEM blocks
- `eyJ...` JSON Web Tokens (three base64url segments)
- `password=...` inline password values
- `ghp_...` GitHub personal access tokens
- `Bearer <token>` bearer tokens

### Output Filter: PII & Credential Rules

| Rule label   | Pattern matched                                  | Replacement              |
|--------------|--------------------------------------------------|--------------------------|
| api_key      | sk-ant-... / sk-...                              | [ANTHROPIC_KEY_REDACTED] |
| aws_key      | AKIA[A-Z0-9]{16}                                 | [AWS_KEY_REDACTED]       |
| jwt          | eyJ...base64...base64...base64                   | [JWT_REDACTED]           |
| private_key  | -----BEGIN...PRIVATE KEY----- blocks             | [PRIVATE_KEY_REDACTED]   |
| github_token | ghp_...                                          | [GITHUB_TOKEN_REDACTED]  |
| ssn          | ddd-dd-dddd (US Social Security)                 | [SSN_REDACTED]           |
| credit_card  | Visa, Mastercard, Amex, Discover BINs            | [CREDIT_CARD_REDACTED]   |
| email        | user@domain.tld                                  | [EMAIL_REDACTED]         |
| phone_us     | (ddd) ddd-dddd, +1 ddd ddd dddd, etc.           | [PHONE_REDACTED]         |
| ipv4         | a.b.c.d                                          | [IPV4_REDACTED]          |

### Budget Enforcer: Configuration

```
Environment variable    Default  Meaning
AVAI_MAX_USD            1.00     Maximum cumulative USD spend per session
AVAI_MAX_RPM            60       Maximum API requests per minute
```

The enforcer uses a **sliding window** rate limiter (`collections.deque`) so
bursts at minute boundaries are prevented.  `BudgetExceededError` is raised
**before** the API call so the budget boundary is always honoured.

### Audit Log Format

Each line in `logs/audit.log` is a JSON object:

```json
{"timestamp": "2026-05-24T10:23:45+00:00", "event": "api_call",
 "session_id": "a1b2c3d4", "model": "haiku",
 "input_tokens": 150, "output_tokens": 300, "cost_usd": 0.0006}

{"timestamp": "2026-05-24T10:23:46+00:00", "event": "sanitize_block",
 "session_id": "a1b2c3d4",
 "blocked_reason": "Secret detected: API key (sk-)",
 "warnings": []}
```

The `session_id` field correlates all events from a single CLI invocation.
The file is append-only; the Auditor never modifies or deletes existing lines.
Write failures are silently swallowed so a disk issue cannot crash the app.

### CCA-F Domain Coverage

| Domain                    | Phase | Demonstrated via                            |
|---------------------------|-------|---------------------------------------------|
| API Fundamentals          |   1   | ClaudeClient, prompt caching, cost tracker  |
| Prompt Engineering        |   1   | PROMPTS dict, templates, system prompt swap |
| Tool Use / Function Call  |   2   | ToolRegistry, ToolExecutor agentic loop     |
| Context Management        |   1   | ConversationManager, auto-summarise         |
| Cost & Model Selection    |   1   | CostTracker, PRICING table, CSV log         |
| Agents & Orchestration    |   3   | ResearchAgent, WriterAgent, Orchestrator    |
| Prompt Chaining           |   3   | 3 chains: linear, refinement, conditional   |
| MCP                       |   4   | FastMCP server, JSON-RPC client, bridge     |
| Safety & Responsible Use  |   5   | sanitizer, filter, budget, audit            |

### Security Notes

1. **sanitizer is not a firewall.**  Regex patterns catch common/obvious
   injection strings.  An attacker who knows the patterns can encode
   around them.  Defence-in-depth: treat the sanitizer as an early-warning
   system, not a security boundary.

2. **filter is best-effort regex.**  PII that appears in unusual formats
   (reversed characters, zero-width joiners, locale-specific separators)
   will not be caught.  Do not rely on it as your sole data-loss-prevention
   control.

3. **budget is per-process.**  Each `avai` invocation starts a fresh
   BudgetEnforcer.  Long-lived processes (web servers) should persist
   BudgetState externally (e.g. Redis) for durable enforcement.

4. **audit is append-only per this process.**  The file is not protected
   against external modification.  For compliance use cases, write to a
   write-once storage backend and add a hash chain.

5. **secrets in audit log.**  filter_tool_result() intentionally does NOT
   log the matched credential text -- only the rule label.  This prevents
   the audit log itself from becoming a secrets store.
