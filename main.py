#!/usr/bin/env python3
"""
main.py — AdoptviaAI CLI entry point

AdoptviaAI — AI adoption done right
CCA-F portfolio project demonstrating Claude API expertise across all domains.

Entry point: cli() Click group -> registered as 'avai' via setup.py

CCA-F Domains demonstrated:
  Phase 1: API Fundamentals, Prompt Engineering, Prompt Caching,
           Cost Management, Context Management
  Phase 2: Tool Use & Function Calling — agentic loop, tool registry
  Phase 3: Agents — BaseAgent, specialised agents, OrchestratorAgent,
           prompt chaining, human-in-the-loop, multi-step reasoning
  Phase 4: MCP — Model Context Protocol, dynamic tool discovery,
           JSON-RPC 2.0 over stdio transport
  Phase 5: Safety & Guardrails — input sanitisation, output filtering,
           budget enforcement, audit logging
"""

import json
import os as _os
import sys
import traceback as _traceback
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Ensure project root is importable when running as `python main.py`
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    COST_WARNING_THRESHOLD,
    DEFAULT_MODEL,
    MAX_TOKENS,
    SYSTEM_PROMPT_DEFAULT,
    validate_api_key,
)
from core.client import MODEL_MAP, ClaudeClient
from core.conversation import ConversationManager
from core.cost_tracker import PRICING, CostTracker
from core.tool_executor import ToolExecutor
from prompts.system_prompts import PROMPTS
from mcp.client import MCPClient
from tools.builtin import BUILTIN_TOOLS
from tools.registry import ToolRegistry

# ── Phase 5: Safety & Guardrails ──────────────────────────────────────────────
# CCA-F Domain: Safety & Responsible Use
# All four safety modules imported at module level so the singletons below
# are initialised before any Click command runs.
from safety.sanitizer import sanitize
from safety.filter    import filter_output, available_rules
from safety.budget    import BudgetEnforcer, BudgetExceededError
from safety.audit     import (
    Auditor, AuditEvent, get_auditor, set_auditor, new_session_id,
)

# ── Version ────────────────────────────────────────────────────────────────────
VERSION = "0.5.0"

# ── Module-level singletons ────────────────────────────────────────────────────
# CCA-F: Process-scoped singletons ensure the budget and audit trail span
# ALL commands in a single avai invocation.

_DEFAULT_MAX_USD = float(_os.getenv("AVAI_MAX_USD", "1.00"))
_DEFAULT_MAX_RPM = int(_os.getenv("AVAI_MAX_RPM", "30"))

# Budget enforcer: checks USD cap + RPM before every API call.
_session_budget = BudgetEnforcer(max_usd=_DEFAULT_MAX_USD, max_rpm=_DEFAULT_MAX_RPM)

# Cost tracker: accumulates token usage across all API calls this session.
# Module-level so the result_callback in _SafeGroup can access it.
_session_tracker = CostTracker()

# Auditor: append-only JSON-lines log at logs/audit.log.
_SESSION_ID  = new_session_id()
_AUDIT_LOG   = str(Path(__file__).parent / "logs" / "audit.log")
_auditor     = Auditor(session_id=_SESSION_ID, log_path=_AUDIT_LOG, enabled=True)
set_auditor(_auditor)

# Blocked-call counter (for safety status display)
_blocked_calls: int = 0

console = Console(legacy_windows=False)


# ══════════════════════════════════════════════════════════════════════════════
# SAFE GROUP — wraps cli() for global error handling + session summary
# ══════════════════════════════════════════════════════════════════════════════

class _SafeGroup(click.Group):
    """
    Click Group subclass providing two cross-cutting behaviours:

    1. Error handling (Task 6):
       Catches unhandled exceptions and prints a friendly Rich error panel
       instead of a raw Python traceback. Pass --verbose / -v to re-enable
       full tracebacks for debugging.

    2. Session cost summary (Task 7):
       Prints a one-line token/cost summary after any command that made at
       least one API call. Runs in a finally block so it appears even when
       the command exits via sys.exit().

    CCA-F Note:
       Subclassing click.Group is the idiomatic way to add cross-cutting
       behaviour (logging, auth, error handling) to all commands in a group
       without modifying each command individually.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except SystemExit:
            raise  # let Click's own sys.exit() propagate normally
        except click.exceptions.Exit:
            raise  # click.exceptions.Exit (from --help / ctx.exit()) is not ClickException
        except click.exceptions.ClickException:
            raise  # Click formats its own errors (UsageError, BadParameter, …)
        except Exception as exc:
            verbose = False
            if isinstance(ctx.obj, dict):
                verbose = ctx.obj.get("verbose", False)
            if verbose:
                console.print_exception()
            else:
                console.print(Panel(
                    f"[red]{exc}[/red]\n\n"
                    "[dim]Run with [bold]-v / --verbose[/bold] for the full traceback.[/dim]",
                    title="[red bold]Error[/red bold]",
                    border_style="red",
                ))
            sys.exit(1)
        finally:
            # Task 7: one-line session cost summary at exit
            # Only shown if at least one API call was made this session.
            if _session_tracker._calls > 0:
                console.print(
                    f"[dim]Session: {_session_tracker._calls} call(s) | "
                    f"{_session_tracker._input:,} in | "
                    f"{_session_tracker._output:,} out | "
                    f"${_session_tracker._total_cost:.4f}[/dim]"
                )


# ── Slash command help ─────────────────────────────────────────────────────────
CHAT_HELP = """\
[bold cyan]Slash Commands[/bold cyan]
  [yellow]/exit[/yellow]                Quit and show cost summary
  [yellow]/model[/yellow] [dim]<name>[/dim]         Switch model: [cyan]haiku[/cyan] | [cyan]sonnet[/cyan] | [cyan]opus[/cyan]
  [yellow]/system[/yellow] [dim]<name>[/dim]        Switch system prompt: [cyan]default[/cyan] | [cyan]technical[/cyan] | [cyan]reviewer[/cyan] | [cyan]business[/cyan]
  [yellow]/clear[/yellow]               Clear conversation history
  [yellow]/summary[/yellow]             Show current session cost
  [yellow]/save[/yellow] [dim][filename][/dim]     Save conversation to JSON (auto-names if omitted)
  [yellow]/dry-run[/yellow]             Toggle dry run mode on/off
  [yellow]/help[/yellow]                Show this message
"""


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _resolve_model_id(shortname: str) -> str:
    return MODEL_MAP.get(shortname, shortname)


def _build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for definition, handler in BUILTIN_TOOLS:
        registry.register(definition, handler)
    return registry


def _dry_run_output(user_input, system_prompt, history, model, max_tokens):
    full_prompt = f"[SYSTEM]\n{system_prompt}\n\n"
    for msg in history:
        content = msg.get("content", "")
        if isinstance(content, str):
            full_prompt += f"[{msg['role'].upper()}]\n{content}\n\n"
        else:
            full_prompt += f"[{msg['role'].upper()}] (tool blocks)\n\n"
    full_prompt += f"[USER]\n{user_input}"

    preview = full_prompt[:2000] + ("..." if len(full_prompt) > 2000 else "")
    console.print(Panel(preview, title="[bold yellow][DRY RUN] Full Prompt[/bold yellow]", border_style="yellow"))

    est_input  = len(full_prompt) // 4
    est_output = max_tokens // 2
    full_id    = _resolve_model_id(model)
    prices     = PRICING.get(full_id, list(PRICING.values())[0])
    est_cost   = est_input / 1_000_000 * prices["input"] + est_output / 1_000_000 * prices["output"]
    console.print(
        f"[yellow][DRY RUN][/yellow] Est. input: [cyan]~{est_input:,}[/cyan] tokens | "
        f"Est. output: [cyan]~{est_output:,}[/cyan] tokens | Est. cost: [green]${est_cost:.5f}[/green]"
    )


def _print_cost_line(tracker, model, usage, call_cost):
    in_tok  = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cw      = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr      = getattr(usage, "cache_read_input_tokens", 0) or 0
    console.print(
        f"[dim]Model: {model} | in: {in_tok:,} out: {out_tok:,} "
        f"cache_write: {cw:,} cache_read: {cr:,} | "
        f"Cost: ${call_cost:.5f} | Session: ${tracker.session_total():.5f}[/dim]"
    )


def _print_agentic_cost_line(tracker, executor):
    console.print(
        f"[dim]Agentic loop: {executor.iterations} API call(s), "
        f"{executor.tool_calls_made} tool invocation(s) | "
        f"Session total: ${tracker.session_total():.5f}[/dim]"
    )


def _parse_kv_inputs(raw_inputs: tuple[str, ...]) -> dict:
    """Parse KEY=VALUE pairs into a dict, auto-converting JSON-parseable values."""
    result = {}
    for raw in raw_inputs:
        if "=" not in raw:
            raise click.BadParameter(f"Use KEY=VALUE format, got: {raw!r}")
        key, _, value = raw.partition("=")
        try:
            result[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            result[key.strip()] = value.strip()
    return result


def _init_session(ctx):
    ctx.obj.setdefault("client", ClaudeClient(model=ctx.obj["model"]))
    ctx.obj["tracker"] = _session_tracker   # always use module-level singleton
    ctx.obj.setdefault("conversation", ConversationManager())


# ── Phase 5 safety pipeline helpers ───────────────────────────────────────────

def _safe_check_input(text: str, strict: bool = False) -> bool:
    """
    Run the sanitizer on user text.

    Prints warnings/block notices. Returns True if text is safe to send.

    CCA-F Domain: Safety & Responsible Use
    """
    result   = sanitize(text, strict=strict)
    auditor  = get_auditor()

    if result.warnings:
        for w in result.warnings:
            console.print(f"[yellow][SAFETY WARN][/yellow] {w}")
        auditor.log_sanitize_warn(result.warnings)

    if not result.safe:
        console.print(
            f"[red bold][SAFETY BLOCK][/red bold] Input blocked: {result.blocked_reason}"
        )
        auditor.log_sanitize_block(result.blocked_reason, result.warnings)
        return False

    return True


def _safe_filter_response(text: str) -> str:
    """
    Apply output filtering.  Returns filtered text (possibly unchanged).

    CCA-F Domain: Safety & Responsible Use
    """
    fr = filter_output(text)
    if not fr.clean:
        labels = ", ".join(sorted(set(lbl for lbl, _ in fr.redactions)))
        console.print(
            f"[dim yellow][SAFETY][/dim yellow] "
            f"[dim]{fr.redaction_count} redaction(s) applied ({labels})[/dim]"
        )
        get_auditor().log_filter_redact(fr.redactions)
    return fr.text


def _safe_budget_check() -> bool:
    """
    Check budget before an API call.

    Returns True if allowed, False if blocked (with message printed).

    CCA-F Domain: Safety & Responsible Use
    """
    global _blocked_calls
    try:
        _session_budget.check_or_raise()
        return True
    except BudgetExceededError as exc:
        _blocked_calls += 1
        console.print(f"[red bold][BUDGET BLOCK][/red bold] {exc.reason}")
        get_auditor().log_budget_block(
            exc.reason,
            total_usd=exc.state.total_usd,
            remaining_usd=exc.state.remaining_usd,
        )
        return False


def _safe_record_call(input_tokens: int, output_tokens: int, model: str) -> None:
    """
    Record a completed API call in the budget enforcer and audit log.

    CCA-F Domain: Safety & Responsible Use
    """
    cost = _session_budget.record(input_tokens, output_tokens, model)
    get_auditor().log_api_call(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )
    if _session_budget.warn_threshold_reached():
        s = _session_budget.summary()
        console.print(
            f"[yellow][BUDGET WARN][/yellow] "
            f"${s['total_usd']:.4f} of ${s['max_usd']:.4f} used ({s['used_pct']}%)"
        )
        get_auditor().log_budget_warn(total_usd=s["total_usd"], max_usd=s["max_usd"])


# ══════════════════════════════════════════════════════════════════════════════
# ROOT CLI GROUP
# ══════════════════════════════════════════════════════════════════════════════

@click.group(
    cls=_SafeGroup,
    invoke_without_command=True,
)
@click.version_option(version=VERSION, prog_name="avai")
@click.option("--model", "-m", default=None, metavar="MODEL",
              help="Model shortname: haiku (default), sonnet, opus")
@click.option("--system", "-s", default=None, metavar="PROMPT",
              help="System prompt: default | technical | reviewer | business")
@click.option("--max-tokens", "-t", type=int, default=None, metavar="N",
              help=f"Max output tokens (default: {MAX_TOKENS})")
@click.option("--dry-run", "-d", is_flag=True, default=False,
              help="Estimate tokens/cost without calling the API")
@click.option("--verbose", "-v", is_flag=True, default=False,
              help="Show full Python tracebacks on error")
@click.pass_context
def cli(ctx, model, system, max_tokens, dry_run, verbose):
    """
    AdoptviaAI -- AI adoption done right

    \b
    Phase 1:  chat | ask | summary | models | prompts
    Phase 2:  tools list | tools run
    Phase 3:  agent list | agent run | chain list | chain run
    Phase 4:  mcp serve | mcp tools | mcp run | mcp status
    Phase 5:  safety status | safety check | safety audit
    Other:    version
    """
    ctx.ensure_object(dict)
    ctx.obj["model"]      = model   or DEFAULT_MODEL
    ctx.obj["system"]     = system  or SYSTEM_PROMPT_DEFAULT
    ctx.obj["max_tokens"] = max_tokens or MAX_TOKENS
    ctx.obj["dry_run"]    = dry_run
    ctx.obj["verbose"]    = verbose

    if ctx.invoked_subcommand is None:
        # ── Task 5: Branded welcome panel ─────────────────────────────────────
        _print_welcome()


def _print_welcome():
    """Print the branded welcome panel (shown when avai is run with no args)."""
    body = (
        f"[bold cyan]AdoptviaAI[/bold cyan]  [bold](avai)[/bold]  "
        f"[dim]v{VERSION}[/dim]\n"
        "[italic dim]AI adoption done right[/italic dim]\n\n"
        "[bold]Commands:[/bold]\n"
        "  [cyan]chat[/cyan]    Interactive multi-turn conversation\n"
        "  [cyan]ask[/cyan]     Single question, single answer\n"
        "  [cyan]tools[/cyan]   List and invoke built-in tools\n"
        "  [cyan]agent[/cyan]   Run autonomous agents (researcher, writer, orchestrator)\n"
        "  [cyan]chain[/cyan]   Run deterministic prompt chains\n"
        "  [cyan]mcp[/cyan]     Model Context Protocol server and bridge\n"
        "  [cyan]safety[/cyan]  Inspect guardrails, budget, and audit log\n\n"
        "[bold]Flags available on any command:[/bold]\n"
        "  [yellow]--dry-run[/yellow]   Preview tokens/cost without API call\n"
        "  [yellow]--safe[/yellow]      Enable safety guardrails (chat/ask)\n"
        "  [yellow]--verbose[/yellow]   Show full tracebacks on error\n\n"
        "[dim]Run [bold]avai --help[/bold] for the full command reference.[/dim]"
    )
    console.print(Panel(body, border_style="cyan", padding=(1, 2)))


# ══════════════════════════════════════════════════════════════════════════════
# OTHER: version command
# ══════════════════════════════════════════════════════════════════════════════

@cli.command()
def version():
    """Show version, phase summary, and CCA-F domain coverage."""
    body = (
        f"[bold cyan]AdoptviaAI[/bold cyan]  [bold](avai)[/bold]  "
        f"[green]v{VERSION}[/green]\n"
        "[italic]Claude API CLI — CCA-F Portfolio Project[/italic]\n\n"
        "[bold]Phases complete:[/bold]\n"
        "  [cyan]Phase 1[/cyan]  API Fundamentals (chat, ask, prompts, caching, cost)\n"
        "  [cyan]Phase 2[/cyan]  Tool Use (tools list/run, agentic loop)\n"
        "  [cyan]Phase 3[/cyan]  Agents & Orchestration (agent, chain)\n"
        "  [cyan]Phase 4[/cyan]  MCP — Model Context Protocol (mcp serve/tools/run/status)\n"
        "  [cyan]Phase 5[/cyan]  Safety & Guardrails (sanitizer, filter, budget, audit)\n\n"
        "[dim]Default model: haiku | Audit log: logs/audit.log[/dim]"
    )
    console.print(Panel(body, title="[bold cyan]avai version[/bold cyan]", border_style="cyan"))


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.command()
@click.argument("initial_message", required=False, default=None)
@click.option("--load", "load_file", default=None, metavar="FILE",
              help="Resume from a saved conversation JSON file")
@click.option("--tools", "use_tools", is_flag=True, default=False,
              help="Enable tool use (Phase 2)")
@click.option("--safe", "use_safe", is_flag=True, default=False,
              help="Enable safety guardrails: sanitize input, filter output, enforce budget")
@click.option("--dry-run", "cmd_dry_run", is_flag=True, default=False,
              help="Preview prompt/cost estimate without making an API call")
@click.pass_context
def chat(ctx, initial_message, load_file, use_tools, use_safe, cmd_dry_run):
    """Start an interactive multi-turn chat session.

    \b
    Optionally pass an INITIAL_MESSAGE to pre-populate the first turn.
    With --dry-run, shows the cost estimate and exits without calling the API.

    \b
    CCA-F Domains: API Fundamentals, Prompt Engineering, Context Management
    Add --safe to activate Phase 5 safety guardrails on every message.
    Add --tools to enable the built-in tool registry.
    """
    validate_api_key()
    _init_session(ctx)

    model       = ctx.obj["model"]
    system_name = ctx.obj["system"]
    max_tokens  = ctx.obj["max_tokens"]
    dry_run     = ctx.obj["dry_run"] or cmd_dry_run  # --dry-run on parent OR on chat itself
    client: ClaudeClient        = ctx.obj["client"]
    tracker: CostTracker        = ctx.obj["tracker"]
    convo:   ConversationManager = ctx.obj["conversation"]
    system_prompt = PROMPTS.get(system_name, PROMPTS["default"])

    tool_registry = _build_tool_registry() if use_tools else None
    executor      = ToolExecutor() if use_tools else None

    if use_safe:
        get_auditor().log_session_start(meta={"command": "chat", "model": model})

    if load_file:
        try:
            convo.load_from_file(load_file)
            console.print(f"[dim]Resumed from [cyan]{load_file}[/cyan] ({convo.message_count()} messages)[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Warning: could not load '{load_file}': {exc}[/yellow]")

    header = (
        f"[bold cyan]AdoptviaAI[/bold cyan]  [dim italic]AI adoption done right[/dim italic]\n"
        f"Model: [green]{model}[/green]  |  System: [green]{system_name}[/green]  |  Max tokens: [green]{max_tokens}[/green]"
    )
    if use_tools:
        header += f"\n[cyan]Tools:[/cyan] [dim]{', '.join(tool_registry.tool_names())}[/dim]"
    if use_safe:
        header += "\n[magenta]Safety:[/magenta] [dim]sanitizer + filter + budget enforcer active[/dim]"
    if dry_run:
        header += "\n[yellow bold][DRY RUN MODE][/yellow bold]"

    console.print(Panel(header, border_style="cyan"))

    # ── Handle initial_message + dry-run shortcut ──────────────────────────────
    # avai chat --safe --dry-run "hello" -> safety check + dry-run output, then exit
    if initial_message and dry_run:
        if use_safe:
            _safe_check_input(initial_message)
        _dry_run_output(initial_message, system_prompt, [], model, max_tokens)
        return

    console.print("[dim]Type [bold]/help[/bold] for commands, [bold]/exit[/bold] to quit.\n[/dim]")

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd   = parts[0].lower()

            if cmd == "/exit":
                break
            elif cmd == "/help":
                console.print(CHAT_HELP)
            elif cmd == "/clear":
                convo.clear()
                console.print("[dim]History cleared.[/dim]")
            elif cmd == "/summary":
                tracker.display_summary()
            elif cmd == "/dry-run":
                dry_run = not dry_run
                console.print(f"[dim]Dry run: {'[yellow]ON[/yellow]' if dry_run else '[green]OFF[/green]'}[/dim]")
            elif cmd == "/model":
                if len(parts) < 2:
                    console.print(f"[dim]Current: [green]{model}[/green] | Usage: /model haiku|sonnet|opus[/dim]")
                else:
                    new_model = parts[1].strip().lower()
                    if new_model not in MODEL_MAP:
                        console.print(f"[red]Unknown model. Available: {', '.join(MODEL_MAP)}[/red]")
                    else:
                        model = new_model
                        client.switch_model(model)
                        console.print(f"[dim]Switched to [green]{model}[/green][/dim]")
            elif cmd == "/system":
                if len(parts) < 2:
                    console.print(f"[dim]Current: [green]{system_name}[/green] | Available: {', '.join(PROMPTS)}[/dim]")
                else:
                    new_sys = parts[1].strip().lower()
                    if new_sys not in PROMPTS:
                        console.print(f"[red]Unknown prompt. Available: {', '.join(PROMPTS)}[/red]")
                    else:
                        system_name   = new_sys
                        system_prompt = PROMPTS[system_name]
                        console.print(f"[dim]Switched to [green]{system_name}[/green][/dim]")
            elif cmd == "/save":
                filename = (parts[1].strip() if len(parts) > 1
                            else f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                try:
                    convo.save_to_file(filename)
                    console.print(f"[dim]Saved to [cyan]{filename}[/cyan][/dim]")
                except Exception as exc:
                    console.print(f"[red]Save failed: {exc}[/red]")
            else:
                console.print(f"[yellow]Unknown command: {cmd} (type /help)[/yellow]")
            continue

        if dry_run:
            _dry_run_output(user_input, system_prompt, convo.get_history(), model, max_tokens)
            continue

        # ── Phase 5: safety pre-checks ─────────────────────────────────────────
        if use_safe:
            if not _safe_check_input(user_input):
                console.print()
                continue
            if not _safe_budget_check():
                console.print()
                continue

        summarized = convo.summarize_if_needed(client)
        if summarized:
            console.print("[dim yellow]~ Conversation auto-summarised.[/dim yellow]")

        convo.add_message("user", user_input)

        if use_tools and tool_registry and executor:
            console.print()
            try:
                final_text, updated_messages = executor.execute(
                    client=client, messages=convo.get_history(),
                    tools_registry=tool_registry, tracker=tracker,
                    system_prompt=system_prompt, model=model, max_tokens=max_tokens,
                )
            except RuntimeError as exc:
                console.print(f"[red bold]API Error:[/red bold] {exc}")
                convo._history.pop()
                continue
            convo.replace_history(updated_messages)
            convo.add_message("assistant", final_text)
            if use_safe:
                final_text = _safe_filter_response(final_text)
            console.print(f"\n[bold blue]Claude:[/bold blue] {final_text}\n")
            _print_agentic_cost_line(tracker, executor)
        else:
            try:
                response = client.send_message(
                    messages=convo.get_history(),
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                )
            except RuntimeError as exc:
                console.print(f"[red bold]API Error:[/red bold] {exc}")
                convo._history.pop()
                continue
            reply = response.content[0].text
            convo.add_message("assistant", reply)
            if use_safe:
                reply = _safe_filter_response(reply)
                in_tok  = getattr(response.usage, "input_tokens",  0) or 0
                out_tok = getattr(response.usage, "output_tokens", 0) or 0
                _safe_record_call(in_tok, out_tok, model)
            console.print(f"\n[bold blue]Claude:[/bold blue] {reply}\n")
            call_cost = tracker.add_call(response.usage, model)
            _print_cost_line(tracker, model, response.usage, call_cost)

        tracker.warn_if_over_threshold(COST_WARNING_THRESHOLD)
        console.print()

    if use_safe:
        s = _session_budget.summary()
        get_auditor().log_session_end(total_usd=s["total_usd"], total_calls=s["total_calls"])
    console.print()
    tracker.display_summary()


@cli.command()
@click.argument("question", nargs=-1, required=True)
@click.option("--tools", "use_tools", is_flag=True, default=False, help="Enable tool use")
@click.option("--safe", "use_safe", is_flag=True, default=False,
              help="Enable safety guardrails: sanitize input, filter output, enforce budget")
@click.pass_context
def ask(ctx, question, use_tools, use_safe):
    """Ask a single question and exit.

    \b
    CCA-F Domain: API Fundamentals
    Add --safe to activate Phase 5 safety guardrails.

    \b
    Examples:
      avai ask what is prompt caching
      avai -m sonnet ask --tools "calculate 999 * 111"
      avai ask --safe "what is machine learning"
    """
    validate_api_key()
    _init_session(ctx)

    question_text = " ".join(question)
    model         = ctx.obj["model"]
    system_name   = ctx.obj["system"]
    max_tokens    = ctx.obj["max_tokens"]
    dry_run       = ctx.obj["dry_run"]
    client: ClaudeClient = ctx.obj["client"]
    tracker: CostTracker = ctx.obj["tracker"]
    system_prompt = PROMPTS.get(system_name, PROMPTS["default"])

    if dry_run:
        _dry_run_output(question_text, system_prompt, [], model, max_tokens)
        return

    # ── Phase 5: safety pre-checks ──────────────────────────────────────────
    if use_safe:
        get_auditor().log_session_start(meta={"command": "ask", "model": model})
        if not _safe_check_input(question_text):
            sys.exit(1)
        if not _safe_budget_check():
            sys.exit(1)

    messages = [{"role": "user", "content": question_text}]

    if use_tools:
        tool_registry = _build_tool_registry()
        executor      = ToolExecutor()
        console.print(f"[dim cyan]Tools: {', '.join(tool_registry.tool_names())}[/dim cyan]\n")
        try:
            final_text, _ = executor.execute(
                client=client, messages=messages, tools_registry=tool_registry,
                tracker=tracker, system_prompt=system_prompt, model=model,
                max_tokens=max_tokens,
            )
        except RuntimeError as exc:
            console.print(f"[red bold]API Error:[/red bold] {exc}")
            sys.exit(1)
        if use_safe:
            final_text = _safe_filter_response(final_text)
        console.print(f"\n{final_text}")
        console.print()
        _print_agentic_cost_line(tracker, executor)
    else:
        try:
            response = client.send_message(
                messages=messages, system_prompt=system_prompt, max_tokens=max_tokens,
            )
        except RuntimeError as exc:
            console.print(f"[red bold]API Error:[/red bold] {exc}")
            sys.exit(1)
        reply = response.content[0].text
        if use_safe:
            reply   = _safe_filter_response(reply)
            in_tok  = getattr(response.usage, "input_tokens",  0) or 0
            out_tok = getattr(response.usage, "output_tokens", 0) or 0
            _safe_record_call(in_tok, out_tok, model)
        console.print(reply)
        call_cost = tracker.add_call(response.usage, model)
        console.print()
        _print_cost_line(tracker, model, response.usage, call_cost)

    tracker.warn_if_over_threshold(COST_WARNING_THRESHOLD)

    if use_safe:
        s = _session_budget.summary()
        get_auditor().log_session_end(total_usd=s["total_usd"], total_calls=s["total_calls"])


@cli.command()
@click.pass_context
def summary(ctx):
    """Show session cost summary."""
    _init_session(ctx)
    ctx.obj["tracker"].display_summary()
    console.print("[dim]Full history: [cyan]logs/usage.log[/cyan][/dim]")


@cli.command()
def models():
    """List available models and pricing."""
    table = Table(title="[bold cyan]Available Models[/bold cyan]", border_style="cyan", show_lines=True)
    table.add_column("Alias",            style="bold yellow", no_wrap=True)
    table.add_column("Model ID",         style="cyan")
    table.add_column("Input\n$/MTok",    justify="right", style="green")
    table.add_column("Output\n$/MTok",   justify="right", style="green")
    table.add_column("Cache\nWrite",     justify="right", style="dim")
    table.add_column("Cache\nRead",      justify="right", style="dim green")
    for alias, full_id in MODEL_MAP.items():
        p      = PRICING.get(full_id, {})
        marker = " [dim](default)[/dim]" if alias == DEFAULT_MODEL else ""
        table.add_row(
            alias + marker, full_id,
            f"${p.get('input', 0):.2f}", f"${p.get('output', 0):.2f}",
            f"${p.get('cache_write', 0):.2f}", f"${p.get('cache_read', 0):.2f}",
        )
    console.print(table)
    console.print("\n[dim]Tip: Start with [bold]haiku[/bold]. Cache reads cost ~10x less than regular input.[/dim]")


@cli.command(name="prompts")
def list_prompts():
    """List available system prompts."""
    _DESCS = {
        "default":   ("General helpful assistant",   "Everyday Q&A, writing"),
        "technical": ("Senior software architect",   "Architecture, APIs, code design"),
        "reviewer":  ("Expert code reviewer",        "Bug finding, security, best practices"),
        "business":  ("Business consultant",         "ROI analysis, AI strategy"),
    }
    table = Table(title="[bold cyan]Available System Prompts[/bold cyan]", border_style="cyan", show_lines=True)
    table.add_column("Name",     style="bold yellow", no_wrap=True)
    table.add_column("Persona",  style="cyan")
    table.add_column("Best For", style="dim")
    for name, (persona, use) in _DESCS.items():
        marker = " [dim](default)[/dim]" if name == SYSTEM_PROMPT_DEFAULT else ""
        table.add_row(name + marker, persona, use)
    console.print(table)
    console.print("\n[dim]Usage: [bold]avai chat -s technical[/bold] or [bold]/system technical[/bold] in chat[/dim]")


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: TOOLS COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.group(name="tools")
def tools_grp():
    """List and directly invoke registered tools (Phase 2)."""


@tools_grp.command(name="list")
def tools_list():
    """Display all registered tools with their parameters."""
    registry = _build_tool_registry()
    table = Table(title="[bold cyan]Registered Tools[/bold cyan]", border_style="cyan", show_lines=True)
    table.add_column("Name",        style="bold yellow", no_wrap=True)
    table.add_column("Description", style="dim", max_width=50)
    table.add_column("Parameters [dim](* = required)[/dim]", style="cyan")
    for tool in registry.list_tools():
        params_str = ", ".join(tool["parameters"]) if tool["parameters"] else "[dim]none[/dim]"
        table.add_row(tool["name"], tool["description"], params_str)
    console.print(table)
    console.print("\n[dim]Enable in chat: [bold]avai chat --tools[/bold][/dim]")


@tools_grp.command(name="run")
@click.argument("tool_name")
@click.option("--input", "raw_inputs", multiple=True, metavar="KEY=VALUE",
              help="Tool input parameter. Repeat for multiple.")
def tools_run(tool_name, raw_inputs):
    """Directly invoke a tool (no API call, no Claude).

    \b
    Examples:
      avai tools run calculator --input "expression=25*48"
      avai tools run get_project_info
      avai tools run save_note --input filename=test.txt --input content=hello
    """
    registry = _build_tool_registry()
    try:
        parsed = _parse_kv_inputs(raw_inputs)
    except click.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    try:
        handler = registry.get_handler(tool_name)
    except KeyError:
        console.print(f"[red]Unknown tool '{tool_name}'. Available: {', '.join(registry.tool_names())}[/red]")
        sys.exit(1)

    import time
    start = time.monotonic()
    try:
        result  = handler(parsed)
        elapsed = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        console.print(f"[red bold]Tool error:[/red bold] {exc}")
        sys.exit(1)

    status = "[green]OK[/green]" if "error" not in result else "[red]FAIL[/red]"
    console.print(Panel(
        json.dumps(result, indent=2, ensure_ascii=False),
        title=f"[bold cyan]Tool: {tool_name}[/bold cyan]  {status}  [dim]({elapsed}ms)[/dim]",
        border_style="cyan",
    ))


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: AGENT COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.group(name="agent")
def agent_grp():
    """Run multi-step agents (Phase 3).

    \b
    Subcommands:
      avai agent list
      avai agent run <name> <goal...>
    """


@agent_grp.command(name="list")
def agent_list():
    """List available agents with their tools and descriptions."""
    table = Table(title="[bold cyan]Available Agents[/bold cyan]", border_style="cyan", show_lines=True)
    table.add_column("Name",           style="bold yellow", no_wrap=True)
    table.add_column("Type",           style="cyan")
    table.add_column("Tools Available",style="dim")
    table.add_column("Best For",       style="dim")

    _AGENT_INFO = [
        ("researcher",   "ResearchAgent",     "file_reader, calculator, get_project_info",
         "Gathering info, reading files, research questions"),
        ("writer",       "WriterAgent",       "save_note, get_project_info",
         "Writing documents, saving formatted output"),
        ("orchestrator", "OrchestratorAgent", "delegates to researcher + writer",
         "Complex goals needing research AND writing"),
    ]
    for name, cls, tools, use in _AGENT_INFO:
        table.add_row(name, cls, tools, use)

    console.print(table)
    console.print(
        "\n[dim]Usage: [bold]avai agent run researcher \"what tools exist?\"[/bold]\n"
        "       [bold]avai agent run orchestrator --confirm \"research and write a summary\"[/bold][/dim]"
    )


@agent_grp.command(name="run")
@click.argument("agent_name")
@click.argument("goal", nargs=-1, required=True)
@click.option("--confirm", is_flag=True, default=False,
              help="Pause after the plan and ask for approval (orchestrator only)")
@click.pass_context
def agent_run(ctx, agent_name, goal, confirm):
    """Run a named agent with a goal.

    \b
    CCA-F Domain: Agents & Orchestration

    \b
    Examples:
      avai agent run researcher "what tools are available in this project?"
      avai agent run writer "write a haiku about AI and save it as haiku.txt"
      avai agent run orchestrator "research the project tools and write a summary"
      avai -m sonnet agent run orchestrator --confirm "deep research + write report"
    """
    validate_api_key()
    goal_text  = " ".join(goal)
    model      = ctx.obj.get("model", DEFAULT_MODEL) if ctx.obj else DEFAULT_MODEL
    agent_name = agent_name.lower()

    if agent_name == "researcher":
        from agents.researcher import ResearchAgent
        agent  = ResearchAgent(model=model)
        result = agent.run(goal_text)
    elif agent_name == "writer":
        from agents.writer import WriterAgent
        agent  = WriterAgent(model=model)
        result = agent.run(goal_text)
    elif agent_name == "orchestrator":
        from agents.orchestrator import OrchestratorAgent
        agent  = OrchestratorAgent(model=model)
        result = agent.run(goal_text, confirm=confirm)
    else:
        console.print(
            f"[red]Unknown agent '[bold]{agent_name}[/bold]'. "
            "Available: researcher, writer, orchestrator[/red]"
        )
        sys.exit(1)

    if not result.success:
        console.print(f"[red]Agent failed: {result.error}[/red]")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: CHAIN COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.group(name="chain")
def chain_grp():
    """Run sequential prompt-chaining workflows (Phase 3).

    \b
    Prompt chains differ from agents: YOU control the step sequence.
    """


@chain_grp.command(name="list")
def chain_list():
    """List available prompt chains with required inputs."""
    table = Table(title="[bold cyan]Available Chains[/bold cyan]", border_style="cyan", show_lines=True)
    table.add_column("Name",                   style="bold yellow", no_wrap=True)
    table.add_column("Steps", justify="center",style="cyan")
    table.add_column("Pattern",                style="dim")
    table.add_column("Required --input keys",  style="dim")

    _CHAINS = [
        ("summarize-and-save",    "3",   "linear:    summarize -> format -> save", "content, filename"),
        ("analyze-and-recommend", "3",   "refinement: analyse -> options -> rank", "question"),
        ("validate",              "2-3", "conditional: check -> fix? -> verdict",  "content, criteria"),
    ]
    for name, steps, pattern, inputs in _CHAINS:
        table.add_row(name, steps, pattern, inputs)

    console.print(table)
    console.print(
        "\n[dim]Usage: [bold]avai chain run summarize-and-save "
        "--input content=\"my text\" --input filename=summary.md[/bold][/dim]"
    )


@chain_grp.command(name="run")
@click.argument("chain_name")
@click.option("--input", "raw_inputs", multiple=True, metavar="KEY=VALUE",
              help="Chain input. Repeat for multiple keys.")
@click.pass_context
def chain_run(ctx, chain_name, raw_inputs):
    """Run a prompt chain by name.

    \b
    CCA-F Domain: Agents — Prompt Chaining

    \b
    Examples:
      avai chain run summarize-and-save \\
          --input content="Claude is an AI..." --input filename=claude_summary.md
      avai chain run analyze-and-recommend \\
          --input question="How should we reduce API costs?"
      avai chain run validate \\
          --input content="The sky is green." \\
          --input criteria="Must be factually accurate"
    """
    validate_api_key()
    model = ctx.obj.get("model", DEFAULT_MODEL) if ctx.obj else DEFAULT_MODEL

    try:
        inputs = _parse_kv_inputs(raw_inputs)
    except click.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    from agents.chains import (
        chain_analyze_and_recommend,
        chain_summarize_and_save,
        chain_validate,
    )

    chain_name = chain_name.lower().replace("_", "-")

    try:
        if chain_name == "summarize-and-save":
            _require_keys(inputs, ["content", "filename"], chain_name)
            result = chain_summarize_and_save(
                content=inputs["content"], filename=inputs["filename"], model=model,
            )
            console.print(Panel(
                result["summary"],
                title=f"[bold green]Summary saved to {result['filepath']}[/bold green]",
                border_style="green",
            ))
            console.print(f"[dim]Total cost: ${result['total_cost']:.5f}[/dim]")

        elif chain_name == "analyze-and-recommend":
            _require_keys(inputs, ["question"], chain_name)
            result = chain_analyze_and_recommend(question=inputs["question"], model=model)
            console.print(Panel(result["analysis"],       title="[cyan]Analysis[/cyan]",             border_style="cyan"))
            console.print(Panel(result["recommendations"],title="[cyan]Recommendations[/cyan]",       border_style="cyan"))
            console.print(Panel(result["top_choice"],     title="[bold green]Top Choice[/bold green]",border_style="green"))
            console.print(f"[dim]Total cost: ${result['total_cost']:.5f}[/dim]")

        elif chain_name == "validate":
            _require_keys(inputs, ["content", "criteria"], chain_name)
            result       = chain_validate(content=inputs["content"], criteria=inputs["criteria"], model=model)
            status_color = "green" if result["passed"] else "red"
            status_label = "PASS"  if result["passed"] else "FAIL"
            console.print(Panel(result["issues"], title="[cyan]Issues Found[/cyan]", border_style="cyan"))
            if result["suggestions"]:
                console.print(Panel(result["suggestions"], title="[cyan]Suggestions[/cyan]", border_style="cyan"))
            console.print(Panel(
                result["verdict"],
                title=f"[bold {status_color}]Verdict: {status_label}[/bold {status_color}]",
                border_style=status_color,
            ))
            console.print(f"[dim]Total cost: ${result['total_cost']:.5f}[/dim]")

        else:
            console.print(
                f"[red]Unknown chain '{chain_name}'. "
                "Available: summarize-and-save, analyze-and-recommend, validate[/red]"
            )
            sys.exit(1)

    except RuntimeError as exc:
        console.print(f"[red bold]Chain error:[/red bold] {exc}")
        sys.exit(1)


def _require_keys(inputs: dict, keys: list[str], chain_name: str) -> None:
    """Raise UsageError if any required key is missing from inputs."""
    missing = [k for k in keys if k not in inputs]
    if missing:
        raise click.UsageError(
            f"Chain '{chain_name}' requires: {keys}. "
            f"Missing: {missing}. Use --input KEY=VALUE for each."
        )


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4: MCP COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.group(name="mcp")
def mcp_grp():
    """MCP (Model Context Protocol) commands (Phase 4).

    \b
    MCP decouples tool providers from consumers:
      avai mcp serve   -- start the local MCP server
      avai mcp tools   -- list tools/resources dynamically
      avai mcp run     -- run a goal using MCP-discovered tools
      avai mcp status  -- check server reachability
    """


@mcp_grp.command(name="serve")
def mcp_serve():
    """Start the AdoptviaAI MCP server in foreground (stdio transport)."""
    import subprocess
    from config.settings import MCP_SERVER_NAME, MCP_SERVER_SCRIPT

    info = (
        f"[bold]Server:[/bold]    {MCP_SERVER_NAME}\n"
        f"[bold]Script:[/bold]    {MCP_SERVER_SCRIPT}\n"
        f"[bold]Transport:[/bold] stdio\n\n"
        "[bold cyan]Tools:[/bold cyan]\n"
        "  notes_list      -- list all saved notes\n"
        "  notes_read      -- read a note by filename\n"
        "  notes_write     -- create or append to a note\n"
        "  notes_delete    -- delete a note by filename\n"
        "  project_status  -- get project phase/version info\n\n"
        "[bold cyan]Resources:[/bold cyan]\n"
        "  notes://list           -- directory listing\n"
        "  notes://{filename}     -- read a specific note\n\n"
        "[dim]Press Ctrl+C to stop the server.[/dim]"
    )
    console.print(Panel(info, title="[bold magenta]MCP Server[/bold magenta]", border_style="magenta"))

    try:
        proc = subprocess.Popen(
            [sys.executable, MCP_SERVER_SCRIPT],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        console.print(f"[dim]Server started (PID {proc.pid}). Waiting for connections…[/dim]")
        proc.wait()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping server...[/dim]")
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass
        console.print("[dim]Server stopped.[/dim]")
    except FileNotFoundError:
        console.print(f"[red]Error: server script not found at {MCP_SERVER_SCRIPT}[/red]")
        sys.exit(1)


@mcp_grp.command(name="tools")
def mcp_tools():
    """Connect to MCP server and list tools and resources.

    \b
    CCA-F Domain: MCP — Dynamic Tool Discovery
    """
    import asyncio
    from config.settings import MCP_SERVER_NAME, MCP_SERVER_SCRIPT

    async def _fetch():
        async with MCPClient(MCP_SERVER_SCRIPT) as client:
            tools     = await client.list_tools()
            resources = await client.list_resources()
            return tools, resources

    validate_api_key()
    console.print(f"[dim cyan]Connecting to {MCP_SERVER_NAME}...[/dim cyan]")
    try:
        tools, resources = asyncio.run(_fetch())
    except Exception as exc:
        console.print(f"[red bold]Failed to connect:[/red bold] {exc}")
        sys.exit(1)

    tools_table = Table(
        title=f"[bold cyan]MCP Tools ({MCP_SERVER_NAME})[/bold cyan]",
        border_style="cyan", show_lines=True,
    )
    tools_table.add_column("Name",       style="bold yellow", no_wrap=True)
    tools_table.add_column("Description",style="dim", max_width=60)
    tools_table.add_column("Parameters", style="cyan")
    for tool in tools:
        props      = tool.input_schema.get("properties", {})
        params_str = ", ".join(props.keys()) if props else "[dim]none[/dim]"
        tools_table.add_row(tool.name, tool.description, params_str)
    console.print(tools_table)

    if resources:
        res_table = Table(
            title=f"[bold magenta]MCP Resources ({MCP_SERVER_NAME})[/bold magenta]",
            border_style="magenta", show_lines=True,
        )
        res_table.add_column("URI",        style="bold yellow", no_wrap=True)
        res_table.add_column("Name",       style="cyan")
        res_table.add_column("Description",style="dim", max_width=60)
        for res in resources:
            res_table.add_row(res.uri, res.name, res.description)
        console.print(res_table)
    else:
        console.print("[dim]No resources returned.[/dim]")

    console.print(
        f"\n[dim]Connected to [cyan]{MCP_SERVER_NAME}[/cyan] | "
        f"{len(tools)} tools, {len(resources)} resources[/dim]"
    )


@mcp_grp.command(name="run")
@click.argument("goal", nargs=-1, required=True)
@click.pass_context
def mcp_run(ctx, goal):
    """Run a goal using tools discovered from the MCP server.

    \b
    CCA-F Domain: MCP — Dynamic Tool Discovery + Agentic Loop

    \b
    Examples:
      avai mcp run "list all my notes"
      avai mcp run "read the avai_overview note and summarize it"
      avai mcp run "what is the current project status?"
    """
    validate_api_key()
    goal_text = " ".join(goal)
    model     = ctx.obj.get("model", DEFAULT_MODEL) if ctx.obj else DEFAULT_MODEL

    from mcp.bridge import MCPBridge
    bridge = MCPBridge(model=model)

    console.print(Panel(
        f"[bold]Goal:[/bold] {goal_text}",
        title="[bold magenta]MCP Bridge[/bold magenta]",
        border_style="magenta",
    ))

    result = bridge.run(goal_text)

    if not result.success:
        console.print(f"[red bold]MCP Bridge error:[/red bold] {result.error}")
        sys.exit(1)

    console.print(Panel(
        result.output,
        title="[bold green]MCP Bridge -- Result[/bold green]",
        border_style="green",
    ))
    used_str = ", ".join(result.tools_used) if result.tools_used else "none"
    console.print(
        f"[dim]MCP tools used: [cyan]{used_str}[/cyan] | Cost: ${result.total_cost:.5f}[/dim]"
    )


@mcp_grp.command(name="status")
def mcp_status():
    """Show MCP server config and check reachability."""
    import asyncio
    from config.settings import MCP_SERVER_NAME, MCP_SERVER_SCRIPT, MCP_TIMEOUT

    cfg_table = Table(title="[bold cyan]MCP Configuration[/bold cyan]", border_style="cyan", show_lines=True)
    cfg_table.add_column("Setting", style="bold")
    cfg_table.add_column("Value",   style="cyan")
    cfg_table.add_row("Server name",  MCP_SERVER_NAME)
    cfg_table.add_row("Server script",MCP_SERVER_SCRIPT)
    cfg_table.add_row("Timeout",      f"{MCP_TIMEOUT}s")
    cfg_table.add_row("Transport",    "stdio")
    console.print(cfg_table)

    async def _probe():
        async with MCPClient(MCP_SERVER_SCRIPT) as client:
            tools         = await client.list_tools()
            resources     = await client.list_resources()
            status_result = await client.call_tool("project_status", {})
            return tools, resources, status_result

    console.print("\n[dim]Probing server...[/dim]")
    try:
        tools, resources, status = asyncio.run(_probe())
        console.print("[green]Server reachable[/green]")

        s_table = Table(title="[bold green]Server Status[/bold green]", border_style="green", show_lines=True)
        s_table.add_column("Field", style="bold")
        s_table.add_column("Value", style="green")
        if isinstance(status, dict):
            for key, val in status.items():
                s_table.add_row(key, str(val))
        s_table.add_row("tools_available",     str(len(tools)))
        s_table.add_row("resources_available", str(len(resources)))
        console.print(s_table)

    except Exception as exc:
        console.print(f"[red]Server not reachable:[/red] {exc}")
        console.print("[dim]To start the server run: [bold]avai mcp serve[/bold][/dim]")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5: SAFETY COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@cli.group()
def safety():
    """Safety guardrails inspection and testing (Phase 5).

    \b
    CCA-F Domain: Safety & Responsible Use
    These commands let you inspect the safety pipeline without making API calls.

    \b
    Subcommands:
      avai safety status         -- show budget state and safety config
      avai safety check <text>   -- run sanitizer + filter on text
      avai safety audit          -- tail the audit log
    """
    pass


@safety.command("status")
def safety_status():
    """Show the current safety configuration and session budget state.

    \b
    CCA-F Domain: Safety & Responsible Use
    No API call is made.
    """
    s = _session_budget.summary()

    # Budget table
    pct_color = (
        "red"    if s["used_pct"] >= 80 else
        "yellow" if s["used_pct"] >= 50 else
        "green"
    )
    budget_table = Table(
        title="[bold magenta]Safety -- Session Budget[/bold magenta]",
        border_style="magenta", show_lines=True,
    )
    budget_table.add_column("Setting", style="bold")
    budget_table.add_column("Value",   style="cyan")
    budget_table.add_row("Max USD / session",
                         f"${s['max_usd']:.4f}")
    budget_table.add_row("Max req / min",
                         str(s["max_rpm"]))
    budget_table.add_row("Spent this session",
                         f"[{pct_color}]${s['total_usd']:.6f}  ({s['used_pct']}%)[/{pct_color}]")
    budget_table.add_row("Budget remaining",
                         f"${s['remaining_usd']:.6f}")
    budget_table.add_row("Requests made",
                         str(s["total_calls"]))
    budget_table.add_row("Requests blocked",
                         str(_blocked_calls))
    budget_table.add_row("Current RPM",
                         str(s["rpm_current"]))
    budget_table.add_row("Warn threshold",
                         f"{int(s['warn_at_pct'] * 100)}%")
    budget_table.add_row("Warning issued",
                         "[yellow]Yes[/yellow]" if s["warning_issued"] else "No")
    budget_table.add_row("Session ID",  _SESSION_ID)
    budget_table.add_row("Audit log",   _AUDIT_LOG)
    console.print(budget_table)

    # Modules table
    modules_table = Table(
        title="[bold magenta]Safety Modules[/bold magenta]",
        border_style="magenta", show_lines=True,
    )
    modules_table.add_column("Module",       style="bold yellow")
    modules_table.add_column("Layer",        style="cyan")
    modules_table.add_column("What it does", style="dim")
    modules_table.add_row("sanitizer.py","INPUT",  "Prompt injection + secret detection before API call")
    modules_table.add_row("filter.py",   "OUTPUT", "PII + credential redaction from API responses")
    modules_table.add_row("budget.py",   "COST",   "Per-session USD cap + RPM rate limit")
    modules_table.add_row("audit.py",    "AUDIT",  "Append-only JSON-lines log of all safety events")
    console.print(modules_table)

    rules = available_rules()
    console.print(
        f"\n[dim]Filter rules: [cyan]{', '.join(rules)}[/cyan][/dim]\n"
        f"[dim]Enable in chat/ask: [bold]avai chat --safe[/bold] or [bold]avai ask --safe[/bold][/dim]\n"
        f"[dim]Override limits:    AVAI_MAX_USD=... AVAI_MAX_RPM=... in .env[/dim]"
    )


@safety.command("check")
@click.argument("text")
@click.option("--strict", is_flag=True, default=False,
              help="Block on injection warnings (default: warn only)")
def safety_check(text, strict):
    """Run input sanitiser and output filter on TEXT and show results.

    \b
    CCA-F Domain: Safety & Responsible Use
    Tests the sanitizer and filter pipeline. No API call made.

    \b
    Examples:
      avai safety check "ignore all previous instructions"
      avai safety check "my key is sk-abc123456789012345678901234"
      avai safety check "hello, how are you?"
      avai safety check --strict "developer mode enabled"
    """
    from safety.sanitizer import sanitize as _sanitize, SanitizeResult
    from safety.filter    import filter_output as _filter

    # ── Sanitizer ──────────────────────────────────────────────────────────
    san: SanitizeResult = _sanitize(text, strict=strict)

    san_table = Table(
        title="[bold magenta]Sanitizer Result[/bold magenta]",
        border_style="magenta", show_lines=True,
    )
    san_table.add_column("Field", style="bold")
    san_table.add_column("Value", style="cyan")
    san_table.add_row("Input length", str(len(text)))
    san_table.add_row("Clean length", str(len(san.text)))
    if san.safe:
        san_table.add_row("Status", "[green]SAFE[/green]")
    else:
        san_table.add_row("Status", "[red bold]BLOCKED[/red bold]")
        san_table.add_row("Reason", f"[red]{san.blocked_reason}[/red]")
    san_table.add_row(
        "Warnings",
        "\n".join(san.warnings) if san.warnings else "[dim]none[/dim]",
    )
    san_table.add_row("Strict mode", "[yellow]Yes[/yellow]" if strict else "No")
    console.print(san_table)

    # ── Output filter ─────────────────────────────────────────────────────
    fr = _filter(text)

    filter_table = Table(
        title="[bold magenta]Output Filter Result[/bold magenta]",
        border_style="magenta", show_lines=True,
    )
    filter_table.add_column("Field", style="bold")
    filter_table.add_column("Value", style="cyan")
    filter_table.add_row("Original length", str(fr.original_length))
    filter_table.add_row("Redacted length", str(fr.redacted_length))
    if fr.clean:
        filter_table.add_row("Status", "[green]CLEAN[/green]")
    else:
        filter_table.add_row("Status", f"[yellow]{len(fr.redactions)} redaction(s)[/yellow]")
        for label, matched in fr.redactions:
            truncated = matched[:20] + "..." if len(matched) > 20 else matched
            filter_table.add_row(f"  [{label}]", f"[dim]{truncated!r}[/dim]")
    console.print(filter_table)

    # ── Verdict ───────────────────────────────────────────────────────────
    if san.safe and fr.clean:
        console.print("[green bold]Overall: PASS[/green bold] -- text is clean.")
    elif not san.safe:
        console.print("[red bold]Overall: BLOCK[/red bold] -- sanitizer blocked this input.")
    else:
        console.print("[yellow bold]Overall: WARN[/yellow bold] -- output filter would redact content.")


@safety.command("audit")
@click.option("--lines", "-n", default=20, show_default=True,
              help="Number of recent records to show")
@click.option("--event", "-e", default=None, metavar="TYPE",
              help="Filter by event type: api_call | sanitize_block | sanitize_warn | "
                   "filter_redact | budget_block | budget_warn | session_start | session_end")
def safety_audit(lines, event):
    """Show recent entries from the safety audit log.

    \b
    CCA-F Domain: Safety & Responsible Use
    The audit log is append-only JSON-lines at logs/audit.log.

    \b
    Examples:
      avai safety audit
      avai safety audit --lines 50
      avai safety audit --event sanitize_block
      avai safety audit --event api_call
    """
    log_path = Path(_AUDIT_LOG)

    if not log_path.exists():
        console.print(
            "[dim]Audit log does not exist yet. "
            "Run [bold]avai chat --safe[/bold] or [bold]avai ask --safe[/bold] to generate events.[/dim]"
        )
        return

    records = []
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        console.print(f"[red]Could not read audit log: {exc}[/red]")
        return

    if not records:
        console.print("[dim]Audit log is empty.[/dim]")
        return

    if event:
        records = [r for r in records if r.get("event") == event]
        if not records:
            console.print(f"[dim]No records with event=[cyan]{event}[/cyan].[/dim]")
            return

    total_in_file = len(records)
    records = records[-lines:]

    _COLORS = {
        "api_call":       "green",
        "sanitize_block": "red",
        "sanitize_warn":  "yellow",
        "filter_redact":  "yellow",
        "budget_block":   "red",
        "budget_warn":    "yellow",
        "session_start":  "cyan",
        "session_end":    "cyan",
    }

    table = Table(
        title=f"[bold magenta]Audit Log (last {len(records)} of {total_in_file})[/bold magenta]",
        border_style="magenta", show_lines=True,
    )
    table.add_column("Time",     style="dim",       no_wrap=True)
    table.add_column("Event",    style="bold cyan",  no_wrap=True)
    table.add_column("Model",    style="green",      no_wrap=True)
    table.add_column("Cost USD", style="green",      no_wrap=True, justify="right")
    table.add_column("Details",  style="dim")

    for rec in records:
        ts     = rec.get("timestamp", "")[:19].replace("T", " ")
        evt    = rec.get("event", "")
        color  = _COLORS.get(evt, "white")
        model  = rec.get("model") or ""
        cost   = rec.get("cost_usd")
        cost_s = f"${cost:.6f}" if cost is not None else ""

        parts = []
        if evt == "api_call":
            parts.append(f"in={rec.get('input_tokens',0)} out={rec.get('output_tokens',0)}")
        elif evt in ("sanitize_block", "sanitize_warn"):
            reason = rec.get("blocked_reason") or ""
            warns  = rec.get("warnings") or []
            if reason:
                parts.append(reason[:60])
            if warns:
                parts.append(f"warns={len(warns)}")
        elif evt == "filter_redact":
            labels = rec.get("redaction_labels") or []
            parts.append(f"count={rec.get('redaction_count',0)}  {labels}")
        elif evt in ("budget_block", "budget_warn"):
            parts.append(f"total=${rec.get('total_usd',0):.4f} remaining=${rec.get('remaining_usd',0):.4f}")
        elif evt in ("session_start", "session_end"):
            meta = rec.get("meta") or {}
            if meta:
                parts.append(str(meta))

        table.add_row(
            ts,
            f"[{color}]{evt}[/{color}]",
            model,
            cost_s,
            "  ".join(parts) or "[dim]--[/dim]",
        )

    console.print(table)
    console.print(f"[dim]Log: [cyan]{_AUDIT_LOG}[/cyan][/dim]")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
