"""LLM code generation and repair (openpyxl) with structural graph context."""

import logging
import re
from typing import Optional

from prompt import CODE_GEN_SYSTEM, CODE_GEN_PROMPT, CODE_REPAIR_SYSTEM, CODE_REPAIR_PROMPT

from .graph import format_graph_for_prompt, format_schema_for_prompt
from .questions import format_grounding_hint, get_decomposition_items

logger = logging.getLogger(__name__)


def _extract_code(response: str) -> str:
    match = re.search(r"```python\s*(.*?)```", response, re.DOTALL)
    return match.group(1).strip() if match else response.strip()


def _format_decomposed_questions(question_decomposition_json: Optional[dict]) -> str:
    items = get_decomposition_items(question_decomposition_json or {})
    if not items:
        return "(No decomposition steps available)"
    return "\n".join(f"- {s}" for s in items)


def build_code_gen_prompt(
    instruction,
    input_path,
    output_path,
    schema_graph_json,
    question_decomposition_json=None,
    question_grounding_json=None,
):
    # Paths live under ## Steps (question_str). Table/sheet positions live in graph nodes
    # (layout_fields, ranges); edges alone are not enough for openpyxl row/column targets.
    if schema_graph_json and schema_graph_json.get("nodes"):
        graph_str = (
            format_schema_for_prompt(schema_graph_json)
            + "\n\n"
            + format_graph_for_prompt(schema_graph_json)
        ).strip()
    elif schema_graph_json:
        graph_str = format_graph_for_prompt(schema_graph_json)
    else:
        graph_str = "(No table graph available)"
    steps_body = _format_decomposed_questions(question_decomposition_json)
    task_line = (instruction or "").strip()
    if task_line:
        question_str = f"**Task:** {task_line}\n\n**Decomposed steps:**\n{steps_body}"
    else:
        question_str = f"**Decomposed steps:**\n{steps_body}"
    grounding_str = format_grounding_hint(question_grounding_json or {})
    return CODE_GEN_PROMPT.format(
        input_path=input_path,
        output_path=output_path,
        question_str=question_str,
        graph_str=graph_str,
        grounding_str=grounding_str,
    )


def generate_code(
    instruction,
    input_path,
    output_path,
    schema_graph_json,
    llm,
    question_decomposition_json=None,
    question_grounding_json=None,
):
    """Returns (code_str, prompt_text, response_text)."""
    prompt = build_code_gen_prompt(
        instruction,
        input_path,
        output_path,
        schema_graph_json,
        question_decomposition_json=question_decomposition_json,
        question_grounding_json=question_grounding_json,
    )
    system_prompt = CODE_GEN_SYSTEM
    logger.info("  Calling LLM for code generation...")
    try:
        response = llm.chat(prompt, system_prompt=system_prompt)
    except Exception as e:
        logger.error(f"  Code generation LLM call failed: {e}")
        return "", prompt, ""
    code = _extract_code(response)
    logger.info(f"  Code generated ({len(code)} chars)")
    return code, prompt, response


def build_code_repair_prompt(
    instruction,
    previous_code,
    error_summary,
    code_context,
):
    ctx = code_context or "# No local code context available"
    return CODE_REPAIR_PROMPT.format(
        instruction=instruction,
        previous_code=previous_code,
        error_summary=error_summary,
        code_context=ctx,
    )


def repair_code(
    instruction,
    previous_code,
    error_summary,
    code_context,
    llm,
):
    """Returns (code_str, prompt_text, response_text)."""
    prompt = build_code_repair_prompt(
        instruction=instruction,
        previous_code=previous_code,
        error_summary=error_summary,
        code_context=code_context,
    )
    logger.info("  Calling LLM for code repair...")
    try:
        response = llm.chat(prompt, system_prompt=CODE_REPAIR_SYSTEM)
    except Exception as e:
        logger.error(f"  Code repair LLM call failed: {e}")
        return "", prompt, ""
    code = _extract_code(response)
    logger.info(f"  Repaired code generated ({len(code)} chars)")
    return code, prompt, response
