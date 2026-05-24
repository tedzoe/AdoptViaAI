"""
agents/chains.py — Prompt chaining patterns

CCA-F Domain: Agents — Prompt Chaining (sequential, deterministic pipelines)
Prompt chaining differs from agentic loops in a key way:
  - Agentic loop: Claude decides autonomously what to do next
  - Prompt chain:  YOU decide the sequence; Claude executes each step

When to use chaining vs agents:
  - Chain  → steps are known in advance, deterministic flow, lower cost
  - Agent  → steps are unknown, requires reasoning, adapts to the task

Each chain function here demonstrates a different chaining pattern:
  chain_summarize_and_save   : linear pipeline (A → B → save)
  chain_analyze_and_recommend: refinement pipeline (analyse → recommend → rank)
  chain_validate             : conditional pipeline (check → fix? → verdict)

CCA-F: Chains use prompt caching on the shared system prompt — the same
system prompt is reused across all steps in a chain, so after the first
step the prompt is served from cache at ~10x lower cost.
"""

from rich.console import Console

from config.settings import DEFAULT_MODEL, MAX_TOKENS
from core.client import ClaudeClient
from core.cost_tracker import CostTracker
from tools.builtin import save_note_handler

console = Console(legacy_windows=False)


# ── Shared helper ──────────────────────────────────────────────────────────────

def _call(
    client: ClaudeClient,
    tracker: CostTracker,
    model: str,
    prompt: str,
    system_prompt: str,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """
    Make a single non-tool Claude API call and return the text response.

    CCA-F — Prompt Caching:
      Because all steps in a chain share the same system_prompt string,
      the prompt is written to cache on the first call and served from
      cache on subsequent calls — a 10x cost saving on the stable prefix.
    """
    response = client.send_message(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        max_tokens=max_tokens,
    )
    tracker.add_call(response.usage, model)
    return response.content[0].text


def _step_banner(step_num: int, total: int, description: str) -> None:
    """Print a dim step indicator during chain execution."""
    console.print(
        f"[dim cyan]  Chain step {step_num}/{total}: {description}...[/dim cyan]"
    )


# ── Chain 1: Summarize, format, and save ──────────────────────────────────────

def chain_summarize_and_save(
    content: str,
    filename: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Three-step linear chain: summarise → format as markdown → save to file.

    CCA-F — Prompt Chaining (linear pipeline):
      Each step's output feeds directly into the next step's prompt.
      The chain is deterministic — you control every transition.

      Step 1: Claude condenses the raw content into key points
      Step 2: Claude formats the summary as clean markdown
      Step 3: save_note tool persists the result (no extra API call)

    Returns:
        { summary, filepath, total_cost }
    """
    client = ClaudeClient(model=model)
    tracker = CostTracker()
    system = (
        "You are a concise technical summarizer. "
        "Produce clear, accurate, well-structured summaries."
    )

    console.print(f"[cyan]chain_summarize_and_save[/cyan] | model={model}")

    # Step 1: Summarize
    _step_banner(1, 3, "summarising content")
    summary_raw = _call(
        client, tracker, model,
        f"Summarise the following content, capturing all key points:\n\n{content}",
        system,
    )

    # Step 2: Format as markdown
    _step_banner(2, 3, "formatting as markdown")
    summary_md = _call(
        client, tracker, model,
        f"Format this summary as clean, readable markdown with "
        f"a title and appropriate headings:\n\n{summary_raw}",
        system,
    )

    # Step 3: Save (tool call — no API charge)
    _step_banner(3, 3, f"saving to {filename}")
    save_result = save_note_handler({"filename": filename, "content": summary_md})

    if not save_result.get("saved"):
        raise RuntimeError(f"save_note failed: {save_result.get('error')}")

    console.print(
        f"[dim]Chain complete | 2 API calls | "
        f"Cost: ${tracker.session_total():.5f} | "
        f"Saved: {save_result['filepath']}[/dim]"
    )

    return {
        "summary": summary_md,
        "filepath": save_result["filepath"],
        "total_cost": tracker.session_total(),
    }


# ── Chain 2: Analyse, recommend, rank ─────────────────────────────────────────

def chain_analyze_and_recommend(
    question: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Three-step refinement chain: analyse → generate options → rank and select.

    CCA-F — Prompt Chaining (refinement pipeline):
      Each step REFINES the previous output rather than extending it.
      This pattern is useful when you want Claude to:
        1. Understand the problem deeply
        2. Generate a broad set of options
        3. Apply critical judgment to select the best option

      Step 1: Deep analysis — understand the question and its context
      Step 2: Breadth — generate 3 concrete recommendations
      Step 3: Judgment — rank by feasibility and identify the top choice

    Returns:
        { analysis, recommendations, top_choice, total_cost }
    """
    client = ClaudeClient(model=model)
    tracker = CostTracker()
    system = (
        "You are a clear-thinking analyst. "
        "Be direct, specific, and actionable in all responses."
    )

    console.print(f"[cyan]chain_analyze_and_recommend[/cyan] | model={model}")

    # Step 1: Analysis
    _step_banner(1, 3, "analysing the question")
    analysis = _call(
        client, tracker, model,
        f"Analyse this question thoroughly. Identify key factors, "
        f"constraints, and what a good solution would need to address:\n\n{question}",
        system,
    )

    # Step 2: Generate recommendations
    _step_banner(2, 3, "generating 3 recommendations")
    recommendations = _call(
        client, tracker, model,
        f"Based on this analysis:\n{analysis}\n\n"
        f"Generate exactly 3 concrete, distinct recommendations that address "
        f"the original question. Number them 1, 2, 3.",
        system,
    )

    # Step 3: Rank and select
    _step_banner(3, 3, "ranking by feasibility")
    top_choice = _call(
        client, tracker, model,
        f"Given these 3 recommendations:\n{recommendations}\n\n"
        f"Rank them by feasibility and impact. "
        f"State which is the TOP CHOICE and explain why in 2-3 sentences.",
        system,
    )

    console.print(
        f"[dim]Chain complete | 3 API calls | Cost: ${tracker.session_total():.5f}[/dim]"
    )

    return {
        "analysis": analysis,
        "recommendations": recommendations,
        "top_choice": top_choice,
        "total_cost": tracker.session_total(),
    }


# ── Chain 3: Validate with conditional fix step ────────────────────────────────

def chain_validate(
    content: str,
    criteria: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Conditional chain: check → (fix if needed) → verdict.

    CCA-F — Prompt Chaining (conditional pipeline):
      The middle step is CONDITIONAL — it only runs if step 1 found issues.
      This demonstrates that chains don't have to be strictly linear.
      Conditional steps save API calls (and cost) when they're not needed.

      Step 1: Inspection — check content against criteria, list issues
      Step 2: Suggestions — (only if issues found) propose specific fixes
      Step 3: Verdict — final PASS / FAIL ruling with explanation

    Returns:
        { passed, issues, suggestions, verdict, total_cost }
    """
    client = ClaudeClient(model=model)
    tracker = CostTracker()
    system = (
        "You are a rigorous quality checker. "
        "Evaluate content objectively against specified criteria."
    )

    console.print(f"[cyan]chain_validate[/cyan] | model={model}")

    # Step 1: Check content against criteria
    _step_banner(1, 3, "checking content against criteria")
    issues_text = _call(
        client, tracker, model,
        f"Check the following content against the criteria below.\n"
        f"List each issue you find. If there are no issues, say 'NO ISSUES FOUND'.\n\n"
        f"Content:\n{content}\n\n"
        f"Criteria:\n{criteria}",
        system,
    )

    # Detect whether issues were found (conditional branch)
    no_issues = "no issues" in issues_text.lower() or "no issue" in issues_text.lower()

    # Step 2: Suggest fixes (conditional — skipped if no issues)
    suggestions_text = ""
    if not no_issues:
        _step_banner(2, 3, "generating fix suggestions")
        suggestions_text = _call(
            client, tracker, model,
            f"The following issues were found:\n{issues_text}\n\n"
            f"Suggest a specific, actionable fix for each issue.",
            system,
        )
    else:
        console.print("[dim cyan]  Chain step 2/3: no issues found — skipping fix step[/dim cyan]")

    # Step 3: Final verdict
    _step_banner(3, 3, "producing final verdict")
    verdict_prompt = (
        f"Content:\n{content}\n\n"
        f"Criteria:\n{criteria}\n\n"
        f"Issues found:\n{issues_text}\n"
    )
    if suggestions_text:
        verdict_prompt += f"\nSuggested fixes:\n{suggestions_text}\n"
    verdict_prompt += (
        "\nProvide a final quality verdict. "
        "Start your response with exactly 'VERDICT: PASS' or 'VERDICT: FAIL', "
        "then explain your ruling in 2-3 sentences."
    )

    verdict_text = _call(client, tracker, model, verdict_prompt, system)
    passed = verdict_text.strip().upper().startswith("VERDICT: PASS")

    api_calls = 2 if no_issues else 3
    console.print(
        f"[dim]Chain complete | {api_calls} API calls | "
        f"Cost: ${tracker.session_total():.5f} | "
        f"Result: {'PASS' if passed else 'FAIL'}[/dim]"
    )

    return {
        "passed": passed,
        "issues": issues_text,
        "suggestions": suggestions_text,
        "verdict": verdict_text,
        "total_cost": tracker.session_total(),
    }
