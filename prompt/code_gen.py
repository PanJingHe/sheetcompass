"""Prompts for final Python (openpyxl) code generation."""

CODE_GEN_SYSTEM = (
    "You output exactly one runnable Python script in a single ```python``` fenced block—no other text. "
    "Honor the given input/output paths; load the workbook you write to (usually the copy), edit, then save."
)

CODE_GEN_PROMPT = """## Workbook paths
- **Input (read)**: `{input_path}`
- **Output (write)**: `{output_path}` — script must create/overwrite this file.

## Steps
{question_str}

## Structural graph (sheets / tables / columns)
Table/column nodes include `layout_fields` Prefer these over guessing ranges. Below: nodes, then edges.

{graph_str}

## Grounding
{grounding_str}

## Implementation rules
1. `shutil.copy2(input, output)` then `openpyxl.load_workbook(output, data_only=False)` (or load input and `save(output)` if the task does not require preserving unrelated sheets verbatim).
2. End with `workbook.save(output_path)` using the **output** path above.

Reply with **only** one ```python``` code block."""
