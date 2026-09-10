"""Run graph build → question decomposition → grounding → code generation; emit io_log-shaped dicts."""

import json
from typing import Any, Dict, Optional, Tuple

from .codegen import generate_code
from .graph import build_schema_and_graph
from .questions import decompose_instruction, ground_questions_to_graph


def _trace_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def _empty_schema_graph_trace(xlsx_path: str, use_few_shot: bool) -> str:
    return json.dumps(
        {
            "structural_graph_builder": {
                "input": {
                    "xlsx_path": xlsx_path,
                    "schemas": [],
                    "raw_schema_preview": "",
                },
                "output": {"nodes": [], "edges": []},
            },
            "semantic_enrichment": {
                "used_llm": False,
                "used_few_shot": use_few_shot,
                "prompt": "",
                "response": "",
                "parsed_response": None,
                "updated_node_ids": [],
            },
            "final_graph": {"nodes": [], "edges": []},
        },
        ensure_ascii=False,
        indent=2,
    )


def run_codegen(
    instruction: str,
    input_path: str,
    output_path: str,
    schemas: list,
    llm,
    use_few_shot: bool,
    benchmark: str = "spreadsheetbench",
) -> Tuple[Dict[str, Any], Dict[str, Any], str, str, str]:
    """
    Returns (io_log_slices, schema_graph_json, code, codegen_prompt, codegen_response).
    """
    if schemas:
        graph, sg_prompt, sg_response = build_schema_and_graph(
            schemas=schemas,
            xlsx_path=input_path,
            llm=llm,
            use_few_shot=use_few_shot,
            task_instruction_text=instruction,
            benchmark=benchmark,
        )
    else:
        graph = {"nodes": [], "edges": []}
        sg_prompt = ""
        sg_response = _empty_schema_graph_trace(input_path, use_few_shot)

    io: Dict[str, Any] = {
        "schema_graph": {
            "llm_prompt": sg_prompt,
            "raw_response": sg_response,
            "trace": _trace_json(sg_response),
            "parsed": graph,
        }
    }

    qd_json, qd_prompt, qd_response = decompose_instruction(
        instruction=instruction,
        llm=llm,
        use_few_shot=use_few_shot,
        benchmark=benchmark,
    )
    io["question_decomposition"] = {
        "input_instruction": instruction,
        "llm_prompt": qd_prompt,
        "raw_response": qd_response,
        "trace": _trace_json(qd_response),
        "parsed": qd_json,
        "output": qd_json,
    }

    qg_json, qg_prompt, qg_response = ground_questions_to_graph(
        decomposed_questions_json=qd_json,
        schema_graph_json=graph,
        llm=llm,
        use_few_shot=use_few_shot,
        benchmark=benchmark,
    )
    io["question_grounding"] = {
        "input_questions": qd_json,
        "input_graph": graph,
        "llm_prompt": qg_prompt,
        "raw_response": qg_response,
        "trace": _trace_json(qg_response),
        "parsed": qg_json,
        "output": qg_json,
    }

    code, cg_prompt, cg_response = generate_code(
        instruction=instruction,
        input_path=input_path,
        output_path=output_path,
        schema_graph_json=graph,
        llm=llm,
        question_decomposition_json=qd_json,
        question_grounding_json=qg_json,
    )
    io["code_generation"] = {
        "prompt": cg_prompt,
        "response": cg_response,
        "code": code,
    }

    return io, graph, code, cg_prompt, cg_response
