"""Decompose instructions into sub-questions, then ground them on the structural graph (LLM)."""

import json
import logging
from typing import Optional

from prompt import (
    QUESTION_DECOMPOSITION_PROMPT,
    QUESTION_DECOMPOSITION_SYSTEM,
    QUESTION_GROUNDING_PROMPT,
    QUESTION_GROUNDING_SYSTEM,
)
from prompt.few_shot import json_pair_section

from .llm_json import extract_json_from_text

logger = logging.getLogger(__name__)


def get_decomposition_items(decomposition_json: Optional[dict]) -> list:
    """Prefer `steps` (ordered sub-tasks); fall back to legacy `decomposed_questions`."""
    if not isinstance(decomposition_json, dict):
        return []
    steps = decomposition_json.get("steps")
    if isinstance(steps, list) and steps:
        return steps
    dq = decomposition_json.get("decomposed_questions", [])
    return dq if isinstance(dq, list) else []


def _decompose_prompt(
    instruction: str,
    use_few_shot: bool,
    benchmark: str,
) -> str:
    few = json_pair_section(
        "decomp.json",
        use_few_shot,
        logger,
        benchmark=benchmark,
        input_heading="Example input:",
        output_heading="Example output:",
    )
    return QUESTION_DECOMPOSITION_PROMPT.format(
        instruction=instruction,
        few_shot_section=few,
    )


def _edge_ref(edge: dict) -> str:
    return f"{edge['source']}->{edge['target']}:{edge['type']}"


_STRUCTURAL_EDGE_TYPES = frozenset(
    {"has_table", "has_column", "schema_match", "candidate_transfer"}
)


def _edge_category(edge: dict) -> str:
    """Structural edges come from detection + rules; semantic_link edges from LLM semantic enrichment."""
    t = edge.get("type") or ""
    if t == "semantic_link":
        return "semantic"
    if t in _STRUCTURAL_EDGE_TYPES:
        return "structural"
    return "structural"


def _ground_prompt(
    decomposed_questions_json: dict,
    schema_graph_json: dict,
    use_few_shot: bool,
    benchmark: str,
) -> str:
    dq = json.dumps(
        get_decomposition_items(decomposed_questions_json),
        ensure_ascii=False,
        indent=2,
    )
    gn = json.dumps(schema_graph_json.get("nodes", []), ensure_ascii=False, indent=2)
    ge = json.dumps(
        [
            {
                "ref": _edge_ref(e),
                "description": e.get("description", ""),
                "category": _edge_category(e),
            }
            for e in schema_graph_json.get("edges", [])
        ],
        ensure_ascii=False,
        indent=2,
    )
    few = json_pair_section(
        "ground.json",
        use_few_shot,
        logger,
        benchmark=benchmark,
        input_heading="Example input:",
        output_heading="Example output:",
    )
    return QUESTION_GROUNDING_PROMPT.format(
        few_shot_section=few,
        decomposed_questions=dq,
        graph_nodes=gn,
        graph_edges=ge,
    )


def decompose_instruction(
    instruction: str,
    llm,
    use_few_shot: bool = True,
    benchmark: str = "spreadsheetbench",
):
    """Returns (parsed_json, prompt_text, response_text)."""
    prompt = _decompose_prompt(instruction, use_few_shot, benchmark)
    empty = {"steps": [], "decomposed_questions": []}
    trace = {
        "used_llm": llm is not None,
        "used_few_shot": use_few_shot,
        "input_instruction": instruction,
        "prompt": prompt,
        "response": "",
        "parsed_response": None,
        "final_output": empty,
    }
    if llm is None:
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    logger.info("  Calling LLM for step decomposition...")
    try:
        response = llm.chat(prompt, system_prompt=QUESTION_DECOMPOSITION_SYSTEM)
    except Exception as exc:
        logger.warning("  Step decomposition LLM call failed: %s", exc)
        trace["error"] = str(exc)
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    trace["response"] = response
    parsed = extract_json_from_text(response)
    if not parsed:
        logger.warning("  Failed to parse step decomposition JSON.")
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    raw_list = parsed.get("steps")
    if not isinstance(raw_list, list):
        raw_list = parsed.get("decomposed_questions", [])
    if not isinstance(raw_list, list):
        logger.warning("  Decomposition JSON missing \"steps\" or \"decomposed_questions\" list.")
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    cleaned = [s.strip() for s in raw_list if isinstance(s, str) and s.strip()]
    normalized = {"steps": cleaned, "decomposed_questions": cleaned}
    trace["parsed_response"] = normalized
    trace["final_output"] = normalized
    return normalized, prompt, json.dumps(trace, ensure_ascii=False, indent=2)


def ground_questions_to_graph(
    decomposed_questions_json: dict,
    schema_graph_json: dict,
    llm,
    use_few_shot: bool = True,
    benchmark: str = "spreadsheetbench",
):
    """Returns (parsed_json, prompt_text, response_text)."""
    prompt = _ground_prompt(decomposed_questions_json, schema_graph_json, use_few_shot, benchmark)
    empty = {"question_groundings": []}
    trace = {
        "used_llm": llm is not None,
        "used_few_shot": use_few_shot,
        "input_questions": decomposed_questions_json,
        "input_graph_summary": {
            "num_nodes": len(schema_graph_json.get("nodes", [])),
            "num_edges": len(schema_graph_json.get("edges", [])),
        },
        "prompt": prompt,
        "response": "",
        "parsed_response": None,
        "final_output": empty,
    }
    if llm is None:
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    logger.info("  Calling LLM for step grounding...")
    try:
        response = llm.chat(prompt, system_prompt=QUESTION_GROUNDING_SYSTEM)
    except Exception as exc:
        logger.warning("  Step grounding LLM call failed: %s", exc)
        trace["error"] = str(exc)
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    trace["response"] = response
    parsed = extract_json_from_text(response)
    if not parsed:
        logger.warning("  Failed to parse step grounding JSON.")
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    items = parsed.get("question_groundings", [])
    if not isinstance(items, list):
        logger.warning("  Step grounding JSON missing question_groundings list.")
        return empty, prompt, json.dumps(trace, ensure_ascii=False, indent=2)

    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = item.get("question", "")
        linked_node_ids = item.get("linked_node_ids", [])
        linked_edge_refs = item.get("linked_edge_refs", [])
        rationale = item.get("rationale", "")
        normalized_items.append({
            "question": question.strip() if isinstance(question, str) else "",
            "linked_node_ids": [
                nid.strip()
                for nid in linked_node_ids
                if isinstance(nid, str) and nid.strip()
            ],
            "linked_edge_refs": [
                ref.strip()
                for ref in linked_edge_refs
                if isinstance(ref, str) and ref.strip()
            ],
            "rationale": rationale.strip() if isinstance(rationale, str) else "",
        })

    normalized = {"question_groundings": normalized_items}
    trace["parsed_response"] = normalized
    trace["final_output"] = normalized
    return normalized, prompt, json.dumps(trace, ensure_ascii=False, indent=2)


def format_grounding_hint(question_grounding_json: dict) -> str:
    items = question_grounding_json.get("question_groundings", [])
    if not items:
        return "(No step grounding available)"

    lines = []
    for idx, item in enumerate(items, start=1):
        lines.append(f"### Grounded step {idx}")
        lines.append(f"- Step: {item.get('question', '')}")
        lines.append(f"- Linked nodes: {', '.join(item.get('linked_node_ids', [])) or '(none)'}")
        lines.append(f"- Linked edges: {', '.join(item.get('linked_edge_refs', [])) or '(none)'}")
        lines.append(f"- Rationale: {item.get('rationale', '')}")
        lines.append("")
    return "\n".join(lines).strip()


ground_questions = ground_questions_to_graph
format_question_grounding_for_prompt = format_grounding_hint
