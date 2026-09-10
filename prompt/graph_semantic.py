"""Prompts for LLM semantic enrichment: layered node meanings + inferred semantic links."""

# Closed vocabulary for reproducible experiments / parsing (extend only with versioning).
SEMANTIC_LINK_TYPES = (
    "shared_workbook_goal",  # nodes plausibly serve the same high-level user goal
    "data_flow",  # values or rows from source plausibly feed or update target
    "lookup_or_alignment",  # keys/columns suggest cross-table reference or join
    "partition_or_view",  # same or related entities split, filtered, or mirrored
    "uncertain",  # weak or ambiguous relationship; still justify in rationale
)

SEMANTIC_LINK_TYPES_JSON = ", ".join(f'"{t}"' for t in SEMANTIC_LINK_TYPES)

# Prepended early in SCHEMA_GRAPH_PROMPT: task text for domain intent, not implementation steps.
SEMANTIC_TASK_SECTION = (
    "## Task (domain intent)\n\n"
    "{task_text}\n\n"
    "Use the task only to judge **what entities, keys, and measures matter** (e.g. sales vs prices, which columns feed revenue). "
    "**Ignore procedural spreadsheet instructions** (filter, pivot, chart, formatting, menu steps)—this step describes **data meaning**, not how to edit the workbook.\n\n"
    "Align **semantic_info** and **semantic_links** with that intent plus the structural inputs below.\n\n"
)

SCHEMA_GRAPH_SYSTEM = (
    "You enrich a structural spreadsheet graph: output semantic_info per node id and optional semantic_links. "
    "Ground meanings in **tables_preview** cell values, each node's **structural_role**, cross-table edges, and the Task **intent**. "
    "Do not restate Excel UI operations. Valid JSON only; concrete domain wording, no generic sheet boilerplate."
)

SCHEMA_GRAPH_PROMPT = """You output **node_semantics** (semantic_info per id) and **semantic_links**.

{benchmark_task_section}**Inputs (how to use them):** **Tables preview** is the main signal for **columns**—read literal sample values to infer scale (counts, currency, fractions 0–1 such as discount *rates* vs dollar amounts, dates, text). **Nodes** lists every **id** you must return, with **type** (sheet / table / column) and deterministic **structural_role** (placement, dtype class, ranges—no free-text detector dumps). Infer sheet→table→column containment from **type** and **id** patterns (`S_*`, table ids, `*_C*` columns). **Cross-table mechanical edges** list **schema_match** (likely join keys) and **candidate_transfer** (plausible table-to-table flow)—use them in **semantic_links** for meaning, without copying the edge list verbatim.

**semantic_info depth:** go from broad to narrow—**sheet** = what the worksheet is for in business terms (one clear phrase); **table** = what one row represents and how it relates to other tables; **column** = field role (key, dimension, measure, …) plus value interpretation from preview when possible. Avoid one generic sentence for every node; avoid vague “workflow / staging / groups datasets” unless the task clearly needs it.

## Tables preview (sample rows by column header)

{tables_preview}

## Cross-table mechanical edges (schema_match = likely join key; candidate_transfer = plausible data flow)

{structural_edges}

## Nodes (id, type, name, structural_role — use these ids in your JSON)

{node_context}

{few_shot_section}
## Output rules
1. **node_semantics**: one object per **every** node id in **Nodes**. **semantic_info** is kept downstream—self-contained; **column** rows should reflect **Tables preview** when possible.
2. **semantic_links**: 0–12 edges between **listed ids**; **link_type** must be exactly one of: {semantic_link_type_enum}. No self-loops.

Return JSON only:
{{
  "node_semantics": [{{"id": "<node_id>", "semantic_info": "<text>"}}],
  "semantic_links": [
    {{"source": "<node_id>", "target": "<node_id>", "link_type": "<one of allowed>", "rationale": "<brief evidence-based>"}}
  ]
}}
"""
