"""Prompt templates for instruction decomposition into ordered steps (sub-tasks)."""

STEP_DECOMPOSITION_SYSTEM = (
    "You break the instruction into several ordered steps: each step is an imperative sub-task "
    "(what to do or decide next), not a rhetorical question—no answers or full solutions."
)

QUESTION_DECOMPOSITION_SYSTEM = STEP_DECOMPOSITION_SYSTEM

QUESTION_DECOMPOSITION_PROMPT = """{few_shot_section}
## Instruction

{instruction}

Output JSON only. Steps must be concrete enough for structural grounding (sources, date/filter logic, destinations, validation)—not generic planning.
{{
  "steps": ["<step1>", "<step2>"]
}}
"""
