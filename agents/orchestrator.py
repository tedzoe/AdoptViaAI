"""
agents/orchestrator.py — Orchestrator agent

CCA-F Domain: Agents — meta-level orchestration / multi-agent coordination
The OrchestratorAgent demonstrates the highest level of the agent hierarchy:
it reasons about WHICH agents to use and in WHAT ORDER, then sequences them,
passing context between steps and synthesising a final cohesive response.

Key CCA-F concepts demonstrated:
  - Meta-reasoning (planning before acting)
  - Human-in-the-loop (--confirm flag pauses after showing the plan)
  - Context passing between agent steps
  - Cost aggregation across multiple sub-agents
  - Structured JSON output from Claude (plan extraction)
  - Synthesis pattern (combine multiple outputs into one response)

Design: OrchestratorAgent does NOT extend BaseAgent because it doesn't
run tools directly — it delegates to specialised agents.  It makes its
own Claude API calls only for planning and synthesis.
"""

import json
import re
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel

from core.client import ClaudeClient
from core.cost_tracker import CostTracker

console = Console(legacy_windows=False)

# ── Prompts ────────────────────────────────────────────────────────────────────

# CCA-F — Prompt Engineering:
#   The planner prompt uses a very directive format to maximise the chance
#   Claude returns parseable JSON.  "ONLY valid JSON" and the exact schema
#   are critical.  The system prompt is STABLE for caching.
_PLANNER_SYSTEM = """\
You are an AI orchestration planner. Analyze a goal and create a minimal, effective execution plan.

Available agents and their strengths:
- researcher : Gathers information, reads files, performs calculations, answers research questions
- writer     : Creates formatted markdown documents, saves output files, structures information

Planning rules:
1. Use the minimum number of agents needed to accomplish the goal
2. If the goal requires both research AND writing, put researcher before writer
3. If only one agent type is needed, use just that one
4. Each task must be specific, self-contained, and actionable
5. Pass information between steps using "Context from previous steps" in the task description

You MUST respond with ONLY valid JSON — no markdown, no explanation, no code fences:
{
  "reasoning": "one sentence explaining your plan",
  "plan": [
    {"agent": "researcher", "task": "specific task for this agent"},
    {"agent": "writer", "task": "specific task, noting what context to expect"}
  ]
}\
"""

_SYNTHESIZER_SYSTEM = """\
You are a synthesis expert. Given a goal and the outputs from multiple agents, produce a
single clear, cohesive response that directly answers the original goal.

Rules:
- Integrate all agent outputs naturally — do not just concatenate them
- Do not mention the agents or the multi-step process unless explicitly asked
- Be concise and direct; the reader cares about the answer, not the process
- Use markdown formatting if the content benefits from it\
"""


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """
    Structured result from OrchestratorAgent.

    CCA-F — Agents:
      Rich structured result enables programmatic use (testing, pipelines)
      without parsing text.  `steps` preserves the full audit trail.
    """
    success: bool
    final_output: str
    plan_used: list[dict]
    agents_called: list[str]
    total_cost: float
    steps: list[dict]       # [{agent, task, output, cost, success}]
    error: str = ""


# ── Orchestrator ───────────────────────────────────────────────────────────────

class OrchestratorAgent:
    """
    Meta-agent that plans and coordinates ResearchAgent and WriterAgent.

    CCA-F — Agents, Human-in-the-Loop:
      1. PLAN  — Claude reasons about the goal and returns a JSON plan
      2. CONFIRM — optional pause for human approval (--confirm flag)
      3. EXECUTE — run each sub-agent in order, passing context forward
      4. SYNTHESIZE — Claude fuses all outputs into a final response
    """

    def __init__(self, model: str = "haiku") -> None:
        self.model = model
        self._client: ClaudeClient | None = None
        self._tracker: CostTracker | None = None

    def run(self, goal: str, confirm: bool = False) -> OrchestratorResult:
        """
        Orchestrate agents to accomplish `goal`.

        Args:
            goal:    Natural-language description of what to accomplish.
            confirm: If True, pause after showing the plan and ask the
                     user to approve before executing (human-in-the-loop).
        """
        self._client = ClaudeClient(model=self.model)
        self._tracker = CostTracker()

        console.print(
            Panel(
                f"[bold]Goal:[/bold] {goal}",
                title="[bold magenta]OrchestratorAgent[/bold magenta]",
                border_style="magenta",
            )
        )

        try:
            # ── Step 1: Plan ───────────────────────────────────────────────────
            # CCA-F: Meta-reasoning — decide WHAT to do before doing it
            plan_data = self._create_plan(goal)
            plan_steps = plan_data.get("plan", [])
            reasoning = plan_data.get("reasoning", "")

            # Validate agent names before showing the plan
            plan_steps = self._validate_plan(plan_steps)

            # Display plan to the user
            plan_lines = [
                f"Step {i+1}: [yellow]{s['agent']}[/yellow]  ->  {s['task']}"
                for i, s in enumerate(plan_steps)
            ]
            console.print(
                Panel(
                    "\n".join(plan_lines) + f"\n\n[dim]Reasoning: {reasoning}[/dim]",
                    title="[bold magenta]Orchestration Plan[/bold magenta]",
                    border_style="magenta",
                )
            )

            # ── Human-in-the-loop ──────────────────────────────────────────────
            # CCA-F: Before executing potentially costly/irreversible actions,
            # pause for human approval.  This is critical for production agents.
            if confirm:
                answer = console.input("\nProceed with this plan? [y/n]: ").strip().lower()
                if answer != "y":
                    console.print("[yellow]Execution cancelled by user.[/yellow]")
                    return OrchestratorResult(
                        success=False,
                        final_output="",
                        plan_used=plan_steps,
                        agents_called=[],
                        total_cost=self._tracker.session_total(),
                        steps=[],
                        error="Cancelled by user",
                    )

            # ── Step 2: Execute plan ───────────────────────────────────────────
            # CCA-F: Context passing — each agent receives the accumulated
            # output of all previous agents as additional context.
            executed_steps: list[dict] = []
            context_so_far = ""

            for i, step in enumerate(plan_steps):
                console.print(
                    f"\n[magenta]Executing step {i+1}/{len(plan_steps)}:[/magenta] "
                    f"[yellow]{step['agent']}[/yellow]"
                )

                agent = self._make_agent(step["agent"])
                task = step["task"]

                # Inject context from previous steps
                if context_so_far:
                    task = (
                        f"{task}\n\n"
                        f"--- Context from previous steps ---\n{context_so_far}"
                    )

                result = agent.run(task)

                executed_steps.append(
                    {
                        "agent": step["agent"],
                        "task": step["task"],
                        "output": result.output,
                        "cost": result.total_cost,
                        "success": result.success,
                    }
                )

                if result.success:
                    context_so_far += (
                        f"[{step['agent']} output]:\n{result.output}\n\n"
                    )
                else:
                    console.print(
                        f"[yellow]Step {i+1} failed: {result.error} — continuing[/yellow]"
                    )

            # ── Step 3: Synthesize ─────────────────────────────────────────────
            # CCA-F: Synthesis — one final Claude call merges all agent outputs
            console.print("\n[magenta]Synthesizing final response...[/magenta]")
            final_output = self._synthesize(goal, context_so_far)

            # Aggregate costs: own API calls + all sub-agent costs
            sub_agent_cost = sum(s["cost"] for s in executed_steps)
            total_cost = self._tracker.session_total() + sub_agent_cost
            agents_called = [s["agent"] for s in executed_steps]

            console.print(
                Panel(
                    final_output,
                    title="[bold green]OrchestratorAgent -- Final Output[/bold green]",
                    border_style="green",
                )
            )
            console.print(
                f"[dim]Agents called: {', '.join(agents_called)} | "
                f"Total cost: ${total_cost:.5f}[/dim]"
            )

            return OrchestratorResult(
                success=True,
                final_output=final_output,
                plan_used=plan_steps,
                agents_called=agents_called,
                total_cost=total_cost,
                steps=executed_steps,
            )

        except Exception as exc:
            console.print(f"[red bold]Orchestrator error:[/red bold] {exc}")
            return OrchestratorResult(
                success=False,
                final_output="",
                plan_used=[],
                agents_called=[],
                total_cost=self._tracker.session_total() if self._tracker else 0.0,
                steps=[],
                error=str(exc),
            )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _create_plan(self, goal: str) -> dict:
        """
        Ask Claude to analyse the goal and return a JSON execution plan.

        CCA-F — Structured Output:
          We ask Claude to return ONLY JSON so we can parse it reliably.
          The _parse_json helper handles Claude occasionally wrapping the
          JSON in markdown code fences.
        """
        response = self._client.send_message(
            messages=[
                {
                    "role": "user",
                    "content": f"Create a plan to accomplish this goal: {goal}",
                }
            ],
            system_prompt=_PLANNER_SYSTEM,
            max_tokens=512,
        )
        self._tracker.add_call(response.usage, self.model)
        return _parse_json(response.content[0].text)

    def _validate_plan(self, plan_steps: list[dict]) -> list[dict]:
        """Remove any steps with unrecognised agent names."""
        valid = {"researcher", "writer"}
        validated = [s for s in plan_steps if s.get("agent", "").lower() in valid]
        if not validated:
            raise ValueError(
                f"Plan contained no valid agents. "
                f"Valid agents: {sorted(valid)}"
            )
        return validated

    def _synthesize(self, goal: str, context: str) -> str:
        """One final Claude call to merge all agent outputs into a coherent answer."""
        response = self._client.send_message(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Original goal: {goal}\n\n"
                        f"Agent outputs:\n{context}\n\n"
                        "Provide a single, cohesive final response."
                    ),
                }
            ],
            system_prompt=_SYNTHESIZER_SYSTEM,
            max_tokens=1024,
        )
        self._tracker.add_call(response.usage, self.model)
        return response.content[0].text

    def _make_agent(self, name: str):
        """Instantiate the named agent (lazy import to avoid circulars)."""
        # Import here to avoid module-level circular imports
        from agents.researcher import ResearchAgent
        from agents.writer import WriterAgent

        mapping = {
            "researcher": ResearchAgent,
            "writer": WriterAgent,
        }
        cls = mapping.get(name.lower())
        if cls is None:
            raise ValueError(f"Unknown agent: {name!r}")
        return cls(model=self.model)


# ── JSON parsing helper ────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    """
    Robustly extract a JSON object from Claude's response.

    CCA-F — Structured Output:
      Claude sometimes wraps JSON in markdown code fences even when
      instructed not to.  This helper handles the common cases so the
      planner is resilient to minor formatting variations.
    """
    # 1. Direct parse (ideal case)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 2. Strip ```json ... ``` or ``` ... ``` fences
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Find the first {...} block anywhere in the response
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse a JSON plan from Claude's response.\n"
        f"Response was: {text[:300]}"
    )
