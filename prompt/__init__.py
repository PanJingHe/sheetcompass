"""LLM prompt templates (one module per call site)."""

import os

_DIR = os.path.dirname(os.path.abspath(__file__))


def load_few_shot(name: str, benchmark: str = "spreadsheetbench") -> str:
    """Load a few-shot JSON from prompt/spreadsheet/ or prompt/sheetcopilot/."""
    from .few_shot import FEW_SHOT_DIR_BY_BENCHMARK

    sub = FEW_SHOT_DIR_BY_BENCHMARK.get(benchmark, "spreadsheet")
    with open(os.path.join(_DIR, sub, name), "r", encoding="utf-8") as f:
        return f.read()


from .graph_semantic import SCHEMA_GRAPH_SYSTEM, SCHEMA_GRAPH_PROMPT
from .code_gen import CODE_GEN_SYSTEM, CODE_GEN_PROMPT
from .repair import CODE_REPAIR_SYSTEM, CODE_REPAIR_PROMPT
from .decompose import QUESTION_DECOMPOSITION_SYSTEM, QUESTION_DECOMPOSITION_PROMPT
from .ground import QUESTION_GROUNDING_SYSTEM, QUESTION_GROUNDING_PROMPT
