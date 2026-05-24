"""
agents/base.py — Base agent class

CCA-F Domain: Agents
Demonstrates the fundamental agent pattern:
  - An agent receives a natural-language GOAL (not a rigid command)
  - It autonomously decides which tools to call and in what order
  - It loops until the goal is achieved (stop_reason == "end_turn")
  - It reports cost, steps, and success/failure to the caller

BaseAgent wraps Phase 2's ToolExecutor with:
  - A stable, role-specific system prompt (cached across iterations)
  - A curated tool set matched to the agent's purpose
  - Rich output (panel on start, panel on completion)
  - A structured AgentResult for programmatic use by the orchestrator
"""

from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel

from config.settings import MAX_TOKENS
from core.client import ClaudeClient
from core.cost_tracker import CostTracker
from core.tool_executor import ToolExecutor
from tools.registry import ToolRegistry

console = Console(legacy_windows=False)


@dataclass
class AgentResult:
    """
    Structured result returned by every agent.

    CCA-F — Agents:
      Returning a dataclass (not just a string) lets the Orchestrator
      access cost, step count, and success state without parsing text.
    """
    success: bool
    output: str
    steps_taken: int        # number of API calls made inside the loop
    total_cost: float       # USD cost of all API calls this agent made
    error: str = ""


class BaseAgent:
    """
    Base class for all AdoptviaAI agents.

    Subclasses customise:
      - name          : displayed in panel headers and logs
      - system_prompt : role-specific instructions (stable for caching)
      - tools         : list of (schema_dict, handler_fn) pairs
      - model         : can be overridden per-agent or per-call
      - max_iterations: safety limit on the agentic loop

    CCA-F — Agents vs Tool Use:
      Phase 2's ToolExecutor handles ONE agentic loop for a SINGLE user
      message.  BaseAgent adds a layer of identity (name, role, curated
      tools) and structured reporting.  The Orchestrator adds a layer of
      meta-reasoning on top of that.
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[tuple] | None = None,
        model: str = "haiku",
        max_iterations: int = 10,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.tools: list[tuple] = tools or []
        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens

    # ── Public interface ───────────────────────────────────────────────────────

    def run(self, goal: str) -> AgentResult:
        """
        Execute the agent to accomplish `goal`.

        CCA-F — Agents:
          The caller passes a natural-language goal.  The agent decides
          autonomously which tools to call (and how many times) to
          accomplish it.  This autonomy is what distinguishes an agent
          from a simple tool-use chain.

        Returns AgentResult so the Orchestrator can chain results and
        accumulate costs without parsing Claude's text output.
        """
        console.print(
            Panel(
                f"[bold]Goal:[/bold] {goal}",
                title=f"[bold cyan]{self.name}[/bold cyan]",
                border_style="cyan",
            )
        )

        client = ClaudeClient(model=self.model)
        tracker = CostTracker()
        registry = ToolRegistry()

        for schema, handler in self.tools:
            registry.register(schema, handler)

        executor = ToolExecutor()

        try:
            output = self._execute_loop(goal, client, registry, tracker, executor)
            result = AgentResult(
                success=True,
                output=output,
                steps_taken=executor.iterations,
                total_cost=tracker.session_total(),
            )
        except Exception as exc:
            result = AgentResult(
                success=False,
                output="",
                steps_taken=0,
                total_cost=tracker.session_total(),
                error=str(exc),
            )

        self._display_result(result)
        return result

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _execute_loop(
        self,
        goal: str,
        client: ClaudeClient,
        registry: ToolRegistry,
        tracker: CostTracker,
        executor: ToolExecutor,
    ) -> str:
        """
        Hand the goal to ToolExecutor and return the final text response.

        CCA-F — Agents:
          The agent's system_prompt is the key differentiator here.
          Because it's stable across all iterations of the loop, it
          benefits from prompt caching — even in multi-step runs.
        """
        messages = [{"role": "user", "content": goal}]

        final_text, _ = executor.execute(
            client=client,
            messages=messages,
            tools_registry=registry,
            tracker=tracker,
            system_prompt=self.system_prompt,
            model=self.model,
            max_tokens=self.max_tokens,
            max_iterations=self.max_iterations,
        )

        return final_text

    def _display_result(self, result: AgentResult) -> None:
        """Print a result panel after the agent finishes."""
        if result.success:
            console.print(
                Panel(
                    result.output,
                    title=f"[bold green]{self.name} -- Complete[/bold green]",
                    border_style="green",
                )
            )
            console.print(
                f"[dim]Steps: {result.steps_taken} | "
                f"Cost: ${result.total_cost:.5f}[/dim]"
            )
        else:
            console.print(
                f"[red bold]{self.name} failed:[/red bold] {result.error}"
            )
