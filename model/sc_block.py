"""SheetCopilot: build the task string that matches SpreadsheetBench's single ``instruction`` field."""

from typing import Any, Dict, List

from .tables import TableSchema


def sheetcopilot_instruction_text(sample: Dict[str, Any]) -> str:
    """
    Context + Instructions columns → one task body, equivalent to SpreadsheetBench's ``instruction``.
    Sheet layout is *not* included here; it appears via tables_preview / schema / graph like the other benchmark.
    """
    ctx = (sample.get("context") or "").strip()
    inst = (sample.get("instruction") or "").strip()
    if ctx and inst:
        return f"{ctx}\n\n{inst}"
    return ctx or inst


def build_sheetcopilot_task_block(
    context: str,
    instruction: str,
    schemas: List[TableSchema],
) -> str:
    """Backward-compatible name: returns merged context+instruction only (schemas ignored)."""
    _ = schemas
    return sheetcopilot_instruction_text({"context": context, "instruction": instruction})


build_sc_block = build_sheetcopilot_task_block
