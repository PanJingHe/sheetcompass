"""
Deterministic structural graph construction for spreadsheet schemas.

Builds a graph with only Sheet/Table/Column nodes and four edge types:
has_table, has_column, schema_match, and candidate_transfer.
"""

import json
import logging
import re
from copy import deepcopy
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from openpyxl.utils import get_column_letter

from prompt.few_shot import json_pair_section
from .llm_json import extract_json_from_text
from .tables import (
    ColumnSchema,
    TableSchema,
    build_tables_preview_for_semantic,
    format_raw_schemas,
    _data_columns,
    _data_start_row,
    _full_range,
    _header_row,
)
from prompt.graph_semantic import (
    SCHEMA_GRAPH_PROMPT,
    SCHEMA_GRAPH_SYSTEM,
    SEMANTIC_LINK_TYPES,
    SEMANTIC_LINK_TYPES_JSON,
    SEMANTIC_TASK_SECTION,
)

logger = logging.getLogger(__name__)


# ─────────────────────── Helpers ────────────────────────────────────

def _normalize_text(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokenize_text(text: str) -> List[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return [token for token in re.split(r"[^A-Za-z0-9]+", text.lower()) if token]


def _table_range(schema: TableSchema) -> str:
    start_row = schema.region.start_row
    if schema.region.has_header and schema.num_rows > 0:
        start_row += schema.region.header_rows
    end_row = start_row + max(schema.num_rows - 1, 0)
    return (
        f"{get_column_letter(schema.region.start_col)}{start_row}:"
        f"{get_column_letter(schema.region.end_col)}{end_row}"
    )


def _sheet_node_id(sheet_name: str) -> str:
    normalized = _normalize_text(sheet_name) or "sheet"
    return f"S_{normalized}"


def _column_node_id(table_id: str, column: ColumnSchema) -> str:
    normalized = _normalize_text(column.name) or f"col{column.col_index}"
    return f"{table_id}_C{column.col_index}_{normalized}"


def _sheet_node_description(sheet_name: str, tables: List[TableSchema]) -> dict:
    table_ids = [s.table_id for s in tables]
    table_names_str = ", ".join(table_ids)
    if not tables:
        return {
            "structural_role": f'Worksheet "{sheet_name}" has no detected structured tables.',
        }
    return {
        "structural_role": (
            f'Worksheet "{sheet_name}" contains {len(tables)} detected table(s): {table_names_str}.'
        ),
    }


def _table_description(schema: TableSchema) -> dict:
    column_names = ", ".join(column.name for column in schema.columns[:5])
    if len(schema.columns) > 5:
        column_names += ", ..."
    header_row = _header_row(schema)
    return {
        "structural_role": (
            f'Table "{schema.table_id}" is located in sheet "{schema.region.sheet_name}" at data range '
            f'{_table_range(schema)} and contains {schema.num_rows} data rows with '
            f'{schema.num_cols} columns.'
        ),
        "layout_fields": (
            f'header_row={header_row if header_row is not None else "none"}; '
            f'data_start_row={_data_start_row(schema)}; '
            f'num_data_rows={schema.num_rows}; '
            f'data_columns={_data_columns(schema)}; '
            f'full_range={_full_range(schema.region)}'
        ),
    }


def _column_description(schema: TableSchema, column: ColumnSchema) -> dict:
    return {
        "structural_role": (
            f'Column "{column.name}" belongs to table "{schema.table_id}" in sheet '
            f'"{schema.region.sheet_name}" and appears to be a {column.dtype}-like field.'
        ),
    }


def _dtype_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    flexible = {"mixed", "text", "empty"}
    return left in flexible or right in flexible


def _column_match_score(left: ColumnSchema, right: ColumnSchema) -> float:
    left_norm = _normalize_text(left.name)
    right_norm = _normalize_text(right.name)
    if not left_norm or not right_norm:
        return 0.0

    score = 0.0
    if left_norm == right_norm:
        score = 1.0
    else:
        left_tokens = set(_tokenize_text(left.name))
        right_tokens = set(_tokenize_text(right.name))
        if left_tokens and right_tokens:
            overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
            if overlap >= 0.75:
                score = 0.9
            elif overlap >= 0.5:
                score = 0.75

    if score and not _dtype_compatible(left.dtype, right.dtype):
        score -= 0.2
    return max(score, 0.0)


def _infer_candidate_transfer_directions(
    left: TableSchema,
    right: TableSchema,
    matched_columns: List[Tuple[ColumnSchema, ColumnSchema]],
) -> List[Tuple[str, str, str]]:
    left_names = {_normalize_text(column.name) for column in left.columns if _normalize_text(column.name)}
    right_names = {_normalize_text(column.name) for column in right.columns if _normalize_text(column.name)}
    matched_count = len(matched_columns)
    smaller_schema_size = max(min(len(left_names), len(right_names)), 1)
    overlap_ratio = matched_count / smaller_schema_size

    if matched_count == 0 or overlap_ratio < 0.5:
        return []

    if right_names and right_names < left_names:
        return [(
            left.table_id,
            right.table_id,
            f"{right.table_id} has a schema that is largely a subset of {left.table_id}.",
        )]
    if left_names and left_names < right_names:
        return [(
            right.table_id,
            left.table_id,
            f"{left.table_id} has a schema that is largely a subset of {right.table_id}.",
        )]

    if left.num_rows > right.num_rows:
        return [(
            left.table_id,
            right.table_id,
            f"{left.table_id} has similar columns but more rows than {right.table_id}.",
        )]
    if right.num_rows > left.num_rows:
        return [(
            right.table_id,
            left.table_id,
            f"{right.table_id} has similar columns but more rows than {left.table_id}.",
        )]

    return [
        (
            left.table_id,
            right.table_id,
            f"{left.table_id} and {right.table_id} have highly similar schemas.",
        ),
        (
            right.table_id,
            left.table_id,
            f"{right.table_id} and {left.table_id} have highly similar schemas.",
        ),
    ]


def _format_few_shot_section(use_few_shot: bool, benchmark: str) -> str:
    return json_pair_section(
        "semantic.json",
        use_few_shot,
        logger,
        benchmark=benchmark,
        input_heading="Example input:",
        output_heading="Example output:",
        build_input=lambda inp: {
            "instruction": inp.get("instruction", "").strip(),
            "tables_preview": inp.get("tables_preview", []),
            "structural_edges": inp.get("structural_edges", "(none)"),
            "node_context": inp.get("node_context", []),
        },
    )


def _compact_structural_edges(graph: dict) -> str:
    edges = graph.get("edges", [])
    if not edges:
        return "(none)"
    lines = []
    for e in edges:
        lines.append(f"- {e['source']} -> {e['target']} : {e['type']}")
    return "\n".join(lines)


def _cross_table_edges_for_semantic_prompt(graph: dict) -> str:
    """Only edges not implied by node type + id (drops has_table/has_column repetition)."""
    keep = frozenset({"schema_match", "candidate_transfer"})
    edges = [e for e in graph.get("edges", []) if e.get("type") in keep]
    if not edges:
        return "(none — infer sheet→table→column from node **type** and ids in **Nodes**.)"
    lines = [f"- {e['source']} -> {e['target']} : {e['type']}" for e in edges]
    return "\n".join(lines)


def _build_semantic_prompt(
    graph: dict,
    use_few_shot: bool,
    benchmark: str,
    benchmark_task_section: str = "",
    schemas: Optional[List[TableSchema]] = None,
) -> str:
    nodes_for_llm = []
    for node in graph.get("nodes", []):
        ntype = node.get("type")
        if ntype not in {"sheet", "table", "column"}:
            continue
        description = node.get("description", {})
        if not isinstance(description, dict):
            continue
        nodes_for_llm.append({
            "id": node["id"],
            "type": ntype,
            "name": node["name"],
            "structural_role": description.get("structural_role", ""),
        })

    node_context = json.dumps(nodes_for_llm, ensure_ascii=False, indent=2)
    structural_edges = _cross_table_edges_for_semantic_prompt(graph)
    preview_list = build_tables_preview_for_semantic(
        schemas if schemas is not None else []
    )
    tables_preview = json.dumps(preview_list, ensure_ascii=False, indent=2)
    few_shot_section = _format_few_shot_section(use_few_shot, benchmark)
    return SCHEMA_GRAPH_PROMPT.format(
        few_shot_section=few_shot_section,
        benchmark_task_section=benchmark_task_section or "",
        tables_preview=tables_preview,
        structural_edges=structural_edges,
        node_context=node_context,
        semantic_link_type_enum=SEMANTIC_LINK_TYPES_JSON,
    )


def _apply_node_semantics(graph: dict, semantic_payload: dict) -> Tuple[int, List[str]]:
    """Attach LLM **semantic_info** only (no semantic_guess on graph). Accept legacy keys in JSON payload."""
    items = semantic_payload.get("node_semantics")
    if not isinstance(items, list) or len(items) == 0:
        items = semantic_payload.get("semantic_guesses", [])
    if not isinstance(items, list):
        return 0, []

    node_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    updated = 0
    updated_node_ids: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        node_id = item.get("id")
        text = item.get("semantic_info")
        if text is None:
            text = item.get("semantic_guess")
        node = node_by_id.get(node_id)
        if not node or not isinstance(node.get("description"), dict):
            continue
        if isinstance(text, str) and text.strip():
            desc = node["description"]
            desc["semantic_info"] = text.strip()
            desc.pop("semantic_guess", None)
            desc.pop("evidence_summary", None)
            updated += 1
            updated_node_ids.append(node_id)
    return updated, updated_node_ids


def _strip_legacy_description_fields(graph: dict) -> None:
    """Keep descriptions minimal: drop legacy semantic_guess and evidence_summary keys."""
    for node in graph.get("nodes", []):
        desc = node.get("description")
        if isinstance(desc, dict):
            desc.pop("semantic_guess", None)
            desc.pop("evidence_summary", None)


def _apply_semantic_links(graph: dict, semantic_payload: dict) -> Tuple[int, int, List[str]]:
    """Validate LLM semantic_links and append edges. Returns (applied, dropped_invalid, refs)."""
    allowed = frozenset(SEMANTIC_LINK_TYPES)
    links = semantic_payload.get("semantic_links", [])
    if not isinstance(links, list):
        return 0, 0, []

    node_ids = {n["id"] for n in graph.get("nodes", [])}
    edges = graph.setdefault("edges", [])
    seen = {(e["source"], e["target"], e["type"]) for e in edges}
    applied = 0
    dropped = 0
    refs: List[str] = []

    for item in links:
        if not isinstance(item, dict):
            dropped += 1
            continue
        src = (item.get("source") or "").strip()
        tgt = (item.get("target") or "").strip()
        lt = (item.get("link_type") or "").strip()
        rat = item.get("rationale", "")
        if not src or not tgt or src == tgt:
            dropped += 1
            continue
        if src not in node_ids or tgt not in node_ids:
            dropped += 1
            continue
        if lt not in allowed:
            dropped += 1
            continue
        key = (src, tgt, "semantic_link")
        if key in seen:
            continue
        seen.add(key)
        rationale = rat.strip() if isinstance(rat, str) else ""
        desc = f"[{lt}] {rationale}".strip() if rationale else f"Semantic association ({lt})."
        edges.append({
            "source": src,
            "target": tgt,
            "type": "semantic_link",
            "link_type": lt,
            "description": desc,
        })
        applied += 1
        refs.append(f"{src}->{tgt}:{lt}")

    return applied, dropped, refs


def _enrich_semantic_descriptions(
    graph: dict,
    llm,
    use_few_shot: bool,
    benchmark: str,
    benchmark_task_section: str = "",
    schemas: Optional[List[TableSchema]] = None,
) -> dict:
    trace = {
        "used_llm": llm is not None,
        "used_few_shot": use_few_shot,
        "prompt": "",
        "response": "",
        "parsed_response": None,
        "updated_node_ids": [],
        "semantic_links_applied": 0,
        "semantic_links_dropped_invalid": 0,
        "semantic_link_refs": [],
    }
    if llm is None:
        return trace

    prompt = _build_semantic_prompt(
        graph,
        use_few_shot,
        benchmark,
        benchmark_task_section=benchmark_task_section,
        schemas=schemas,
    )
    trace["prompt"] = prompt
    logger.info("  Calling LLM for semantic enrichment (node_semantics + semantic_links)...")
    try:
        response = llm.chat(prompt, system_prompt=SCHEMA_GRAPH_SYSTEM)
    except Exception as exc:
        logger.warning("  Semantic enrichment LLM call failed: %s", exc)
        trace["error"] = str(exc)
        return trace

    trace["response"] = response
    parsed = extract_json_from_text(response)
    if not parsed:
        logger.warning("  Failed to parse semantic enrichment JSON. Nodes have no LLM semantic_info.")
        return trace

    trace["parsed_response"] = parsed
    updated, updated_node_ids = _apply_node_semantics(graph, parsed)
    trace["updated_node_ids"] = updated_node_ids
    applied, dropped, link_refs = _apply_semantic_links(graph, parsed)
    trace["semantic_links_applied"] = applied
    trace["semantic_links_dropped_invalid"] = dropped
    trace["semantic_link_refs"] = link_refs
    logger.info(
        "  Applied node_semantics to %d node(s); semantic_links applied=%d dropped_invalid=%d.",
        updated,
        applied,
        dropped,
    )
    return trace


def _build_structural_graph(schemas: List[TableSchema]) -> dict:
    nodes: List[dict] = []
    edges: List[dict] = []
    seen_edge_keys = set()
    sheet_to_tables: Dict[str, List[TableSchema]] = defaultdict(list)
    column_ids: Dict[Tuple[str, int], str] = {}
    node_by_id: Dict[str, dict] = {}

    for schema in schemas:
        sheet_to_tables[schema.region.sheet_name].append(schema)

    for sheet_name, sheet_tables in sheet_to_tables.items():
        sheet_id = _sheet_node_id(sheet_name)
        nodes.append({
            "id": sheet_id,
            "type": "sheet",
            "name": sheet_name,
            "description": _sheet_node_description(sheet_name, sheet_tables),
        })
        node_by_id[sheet_id] = nodes[-1]

    for schema in schemas:
        sheet_id = _sheet_node_id(schema.region.sheet_name)
        nodes.append({
            "id": schema.table_id,
            "type": "table",
            "name": schema.table_id,
            "description": _table_description(schema),
        })
        node_by_id[schema.table_id] = nodes[-1]
        edges.append({
            "source": sheet_id,
            "target": schema.table_id,
            "type": "has_table",
            "description": f'Table "{schema.table_id}" is located in worksheet "{schema.region.sheet_name}".',
        })

        for column in schema.columns:
            column_id = _column_node_id(schema.table_id, column)
            column_ids[(schema.table_id, column.col_index)] = column_id
            nodes.append({
                "id": column_id,
                "type": "column",
                "name": column.name,
                "description": _column_description(schema, column),
            })
            node_by_id[column_id] = nodes[-1]
            edges.append({
                "source": schema.table_id,
                "target": column_id,
                "type": "has_column",
                "description": f'Column "{column.name}" belongs to table "{schema.table_id}".',
            })

    schema_matches: Dict[Tuple[str, str], List[Tuple[ColumnSchema, ColumnSchema]]] = defaultdict(list)
    for left_idx, left_schema in enumerate(schemas):
        for right_schema in schemas[left_idx + 1:]:
            for left_column in left_schema.columns:
                for right_column in right_schema.columns:
                    score = _column_match_score(left_column, right_column)
                    if score < 0.85:
                        continue
                    left_id = column_ids[(left_schema.table_id, left_column.col_index)]
                    right_id = column_ids[(right_schema.table_id, right_column.col_index)]
                    edge_key = tuple(sorted((left_id, right_id))) + ("schema_match",)
                    if edge_key in seen_edge_keys:
                        continue
                    seen_edge_keys.add(edge_key)
                    schema_matches[(left_schema.table_id, right_schema.table_id)].append((left_column, right_column))
                    edges.append({
                        "source": left_id,
                        "target": right_id,
                        "type": "schema_match",
                        "description": (
                            f'Columns "{left_column.name}" and "{right_column.name}" likely represent '
                            "the same field across different tables."
                        ),
                    })

            matched_columns = schema_matches.get((left_schema.table_id, right_schema.table_id), [])
            for source_id, target_id, reason in _infer_candidate_transfer_directions(
                left_schema,
                right_schema,
                matched_columns,
            ):
                edge_key = (source_id, target_id, "candidate_transfer")
                if edge_key in seen_edge_keys:
                    continue
                seen_edge_keys.add(edge_key)
                shared_names = ", ".join(
                    sorted({left.name for left, _ in matched_columns} | {right.name for _, right in matched_columns})[:4]
                )
                if shared_names:
                    reason = f"{reason} Shared fields include {shared_names}."
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "type": "candidate_transfer",
                    "description": (
                        f'Table "{source_id}" is a plausible source for writing into "{target_id}". '
                        f"{reason}"
                    ),
                })

    return {"nodes": nodes, "edges": edges}


def _format_legacy_schema(schema_data: dict) -> str:
    lines = []
    for table_id, info in schema_data.items():
        lines.append(f"### {table_id}")
        lines.append(f"- Sheet: {info['sheet_name']}")
        lines.append(f"- Range: {info['range']}")
        lines.append(f"- Data rows: {info['num_rows']}, Columns: {info['num_cols']}")
        lines.append("- Columns:")
        for col in info["columns"]:
            lines.append(f"  - {col['name']}: {col.get('description', col.get('dtype', ''))}")
        lines.append("")
    return "\n".join(lines)


def _format_legacy_graph(graph_data: dict) -> str:
    lines = ["## Table Relationship Graph", ""]
    tables = graph_data.get("tables", [])
    lines.append(f"Tables ({len(tables)}): {', '.join(tables)}")
    lines.append("")
    relationships = graph_data.get("relationships", [])
    if not relationships:
        lines.append("No relationships detected between tables.")
        return "\n".join(lines)
    lines.append(f"Relationships ({len(relationships)}):")
    for rel in relationships:
        lines.append(f"  - {rel['source']} -> {rel['target']}: {rel['type']}")
        for linked_col in rel.get("linked_columns", []):
            if isinstance(linked_col, dict):
                lines.append(
                    f"    {linked_col.get('source_col', '')} <-> "
                    f"{linked_col.get('target_col', '')} ({linked_col.get('link_reason', '')})"
                )
        lines.append(f"    Description: {rel.get('description', '')}")
        lines.append("")
    return "\n".join(lines)


def _format_description(description) -> str:
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        structural_role = description.get("structural_role", "")
        semantic = (description.get("semantic_info") or "").strip()
        layout_fields = description.get("layout_fields", "")
        return (
            f"structural_role: {structural_role} "
            f"semantic_info: {semantic} "
            f"layout_fields: {layout_fields}"
        ).strip()
    return str(description)


def format_schema_for_prompt(schema_data: dict) -> str:
    """Format graph nodes or legacy schema JSON into readable text."""
    if "schema" in schema_data:
        return _format_legacy_schema(schema_data["schema"])

    nodes = schema_data.get("nodes", [])
    if not nodes:
        return "(No structural graph nodes available)"

    lines = []
    for node_type in ("sheet", "table", "column"):
        typed_nodes = [node for node in nodes if node.get("type") == node_type]
        if not typed_nodes:
            continue
        lines.append(f"### {node_type.title()} Nodes")
        for node in typed_nodes:
            lines.append(f"- {node['id']} ({node['name']}): {_format_description(node['description'])}")
        lines.append("")
    return "\n".join(lines).strip()


def format_graph_for_prompt(graph_data: dict) -> str:
    """Format graph edges or legacy relationship JSON into readable text."""
    if "table_graph" in graph_data:
        return _format_legacy_graph(graph_data["table_graph"])

    edges = graph_data.get("edges", [])
    if not edges:
        return "No structural graph edges detected."

    lines = [f"Edges ({len(edges)}):"]
    for edge in edges:
        et = edge["type"]
        extra = f" [{edge['link_type']}]" if et == "semantic_link" and edge.get("link_type") else ""
        lines.append(f"- {edge['source']} -> {edge['target']}: {et}{extra}")
        lines.append(f"  Description: {edge['description']}")
    return "\n".join(lines)


# ─────────────────────── Main Function ──────────────────────────────

def build_schema_and_graph(
    schemas,
    xlsx_path,
    llm,
    use_few_shot=True,
    task_instruction_text: Optional[str] = None,
    benchmark: str = "spreadsheetbench",
):
    """
    Deterministic structural graph + optional LLM node_semantics and semantic_link edges.
    When ``task_instruction_text`` is set (SpreadsheetBench: instruction; SheetCopilot: context+instruction),
    the semantic prompt gets the same ``## Task`` section for both benchmarks.
    """
    logger.info("  Building structural graph from detected tables...")

    tt = (task_instruction_text or "").strip()
    bench = SEMANTIC_TASK_SECTION.format(task_text=tt) if tt else ""

    graph = _build_structural_graph(schemas)
    graph_before_semantic_enrichment = deepcopy(graph)
    semantic_trace = _enrich_semantic_descriptions(
        graph=graph,
        llm=llm,
        use_few_shot=use_few_shot,
        benchmark=benchmark,
        benchmark_task_section=bench,
        schemas=schemas,
    )
    _strip_legacy_description_fields(graph)
    trace = {
        "structural_graph_builder": {
            "input": {
                "xlsx_path": xlsx_path,
                "schemas": [schema.to_dict() for schema in schemas],
                "raw_schema_preview": format_raw_schemas(schemas),
            },
            "output": graph_before_semantic_enrichment,
        },
        "semantic_enrichment": semantic_trace,
        "final_graph": graph,
    }
    prompt = semantic_trace.get("prompt", "")
    response = json.dumps(trace, ensure_ascii=False, indent=2)

    logger.info(
        "  Structural graph constructed successfully with %d nodes and %d edges.",
        len(graph["nodes"]),
        len(graph["edges"]),
    )
    return graph, prompt, response


build_graph = build_schema_and_graph
