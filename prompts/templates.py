"""
prompts/templates.py — Reusable prompt templates

CCA-F Domain: Prompt Engineering
Demonstrates:
  - Parameterised prompt construction for repeatable tasks
  - Separation of prompt logic from business logic
  - Named variables make template usage self-documenting
"""

from dataclasses import dataclass, field


@dataclass
class PromptTemplate:
    """
    A parameterised prompt template.

    Attributes:
        name      : Identifier used to look up the template.
        template  : The prompt string with {variable} placeholders.
        variables : List of variable names that must be filled.

    CCA-F — Prompt Engineering:
      Separating the template from the data that fills it encourages
      stable, cacheable prompt structures. The filled variable portions
      appear at the END of the user turn, so the stable prefix (from
      the system prompt) can still be cached effectively.
    """

    name: str
    template: str
    variables: list[str] = field(default_factory=list)

    def fill(self, variables: dict[str, str]) -> str:
        """
        Fill in all {variable} placeholders and return the completed prompt.

        Raises ValueError if a required variable is missing.
        """
        missing = [v for v in self.variables if v not in variables]
        if missing:
            raise ValueError(
                f"Template '{self.name}' is missing required variables: {missing}"
            )
        result = self.template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", value)
        return result


# ── Template registry ──────────────────────────────────────────────────────────

TEMPLATES: dict[str, PromptTemplate] = {
    "summarize": PromptTemplate(
        name="summarize",
        template=(
            "Please summarise the following document concisely.\n"
            "Focus on key points, decisions, and action items.\n\n"
            "Document:\n{document}"
        ),
        variables=["document"],
    ),

    "review": PromptTemplate(
        name="review",
        template=(
            "Please review the following {language} code for bugs, "
            "security issues, and opportunities to improve readability "
            "and performance.\n\n"
            "```{language}\n{code}\n```"
        ),
        variables=["language", "code"],
    ),

    "analyze": PromptTemplate(
        name="analyze",
        template=(
            "Please analyse the following business problem and provide "
            "a structured recommendation with clear rationale.\n\n"
            "Problem:\n{problem}"
        ),
        variables=["problem"],
    ),

    "extract": PromptTemplate(
        name="extract",
        template=(
            "Please extract all {data_type} from the text below.\n"
            "Return the result as a structured list.\n\n"
            "Text:\n{text}"
        ),
        variables=["data_type", "text"],
    ),
}
