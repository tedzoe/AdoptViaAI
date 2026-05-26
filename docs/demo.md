# AdoptviaAI — Command Demo

Example terminal output for the key `avai` commands. All output shown here
is from a live session using the default haiku model.

---

## `avai version`

```
$ avai version

  AdoptviaAI v0.5.0
  "AI adoption done right"

  Phase coverage
  ──────────────────────────────────────────────────────────
  Phase 1  API Fundamentals, Prompt Caching, Cost Tracking    ✓
  Phase 2  Tool Use & Function Calling (agentic loop)         ✓
  Phase 3  Agents & Orchestration (researcher/writer/orch)    ✓
  Phase 4  Model Context Protocol (MCP server + client)       ✓
  Phase 5  Safety & Guardrails (sanitizer/filter/budget)      ✓

  CCA-F domains demonstrated
  ──────────────────────────────────────────────────────────
  Domain 1  API Fundamentals
  Domain 2  Prompt Engineering
  Domain 3  Context Management
  Domain 4  Tool Use & Function Calling
  Domain 5  Agents & Orchestration
  Domain 6  Model Context Protocol
  Domain 7  Safety & Responsible Use

  Default model  claude-haiku-4-5  (set DEFAULT_MODEL in .env to override)
```

---

## `avai ask --safe "What is prompt caching?"`

```
$ avai ask --safe "What is prompt caching?"

  Safety check passed (no injection patterns or secrets detected)

  Prompt caching is a feature of the Claude API that lets you mark parts of
  your prompt — typically the system prompt — for storage in Anthropic's
  infrastructure. On the first request, those tokens are written to cache at
  a slightly higher price (cache_creation_input_tokens). Every subsequent
  request within the 5-minute TTL reads from cache at roughly 10% of the
  normal input token price (cache_read_input_tokens).

  This is especially useful when you have a long, stable system prompt that
  you send with every message. Instead of paying full price for those tokens
  on every turn, you pay the cache-write price once and the cache-read price
  for all subsequent turns — a ~10x reduction in cost for the cached portion.

  AdoptviaAI sends every system prompt with cache_control: {"type":
  "ephemeral"} so this happens automatically.

  Cost  in=312 out=148 cache_write=0 cache_read=287  $0.000142 USD
```

---

## `avai tools run calculator expression="(100 + 50) * 2"`

```
$ avai tools run calculator expression="(100 + 50) * 2"

  Tool: calculator
  ┌─────────────┬──────────────────┐
  │ Input       │ (100 + 50) * 2   │
  │ Result      │ 300              │
  └─────────────┴──────────────────┘

  Tool executed locally — no API call made.
```

---

## `avai agent run researcher "what tools are available in this project?"`

```
$ avai agent run researcher "what tools are available in this project?"

  Agent: ResearchAgent  |  Model: claude-haiku-4-5
  Goal: what tools are available in this project?

  [Turn 1] Sending goal to Claude...
  [Turn 1] Claude requested tool: get_project_info  {}
  [Turn 1] Running tool get_project_info...
  [Turn 1] Tool result: 412 chars — injecting into conversation

  [Turn 2] Claude requested tool: file_reader  {"filepath": "tools/builtin.py", "max_lines": 30}
  [Turn 2] Running tool file_reader...
  [Turn 2] Tool result: 847 chars — injecting into conversation

  [Turn 3] Claude returned final answer (stop_reason=end_turn)

  ──────────────────────────────────────────────────────────
  AdoptviaAI exposes four built-in tools that Claude can call during an
  agentic loop:

  1. calculator — safely evaluate math expressions using AST parsing
     (never raw eval). Supports +, -, *, /, //, %, **.
  2. file_reader — read local files (read-only). Returns content up to
     max_lines, with a truncation flag if the file is longer.
  3. save_note — write text to the notes/ directory. Filenames are
     sanitised to prevent path traversal; subdirectory paths are blocked.
  4. get_project_info — return project metadata (version, commands, models,
     domains) with no inputs required.

  Tools are registered in tools/registry.py and the agentic loop in
  core/tool_executor.py handles the stop_reason == "tool_use" detection,
  local dispatch, and result re-injection automatically.
  ──────────────────────────────────────────────────────────

  Turns: 3  |  Tool calls: 2
  Cost  in=1,204 out=286 cache_write=312 cache_read=0  $0.002187 USD
```

---

## `avai mcp tools`

```
$ avai mcp tools

  Starting MCP server (subprocess)...
  Connected to adoptviaai-server

  Tools exposed by MCP server
  ┌───────────────┬──────────────────────────────────────────────────────────┐
  │ Tool          │ Description                                              │
  ├───────────────┼──────────────────────────────────────────────────────────┤
  │ notes_list    │ List all note files in the notes/ directory              │
  │ notes_read    │ Read the content of a note file by filename              │
  │ notes_write   │ Write or overwrite a note file with the given content    │
  │ notes_delete  │ Delete a note file by filename                           │
  │ project_status│ Return current project status and version information    │
  └───────────────┴──────────────────────────────────────────────────────────┘

  Resources exposed by MCP server
  ┌────────────────────────┬───────────────────────────────────────────────────┐
  │ URI template           │ Description                                       │
  ├────────────────────────┼───────────────────────────────────────────────────┤
  │ notes://list           │ Directory listing of all notes as JSON            │
  │ notes://{filename}     │ Content of a specific note file                   │
  └────────────────────────┴───────────────────────────────────────────────────┘

  MCP server stopped.
```
