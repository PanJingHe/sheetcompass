"""Prompt templates for grounding decomposed questions to the structural graph."""

QUESTION_GROUNDING_SYSTEM = (
    "You map each decomposition step to the best-matching node ids and edge refs from the graph; JSON only."
)

QUESTION_GROUNDING_PROMPT = """{few_shot_section}
The few-shot **input** uses the same three parts as below: **steps**, **graph_nodes**, **graph_edges**.

Each **graph_edges** item has:
- **ref**: `source->target:type` (use this string verbatim in **linked_edge_refs**).
- **description**: human-readable; semantic edges often include a tag like `[data_flow]` or `[lookup_or_alignment]`.
- **category**: **structural** (from table detection + deterministic rules: has_table, has_column, schema_match, candidate_transfer) vs **semantic** (from task-aligned enrichment: type **semantic_link**). Prefer citing **semantic** edges when they express task-specific intent; still cite **structural** edges for containment, column alignment, or heuristic transfer paths when needed.

## Steps
{decomposed_questions}

## Nodes
{graph_nodes}

## Edges
{graph_edges}

Use node **id** and edge **ref** strings exactly as given. Prefer **semantic_info** and **structural_role** inside node descriptions when choosing links. For each grounding item, put the step text in \"question\".

Return JSON only:
{{
  "question_groundings": [
    {{
      "question": "<step text>",
      "linked_node_ids": ["<id>"],
      "linked_edge_refs": ["<src->tgt:type>"],
      "rationale": "<brief>"
    }}
  ]
}}
"""
