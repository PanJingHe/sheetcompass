"""Few-shot JSON blocks embedded in LLM prompts (per-benchmark subfolders under prompt/)."""

import json
import logging
import os
from typing import Callable, Dict, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))

FEW_SHOT_DIR_BY_BENCHMARK: Dict[str, str] = {
    "spreadsheetbench": "spreadsheet",
    "sheetcopilot": "sheetcopilot",
}

InputBuilder = Callable[[Dict], Dict]


def few_shot_subdir(benchmark: str) -> str:
    return FEW_SHOT_DIR_BY_BENCHMARK.get(benchmark, "spreadsheet")


def json_pair_section(
    filename: str,
    use_few_shot: bool,
    logger: logging.Logger,
    *,
    benchmark: str = "spreadsheetbench",
    input_heading: str,
    output_heading: str,
    build_input: Optional[InputBuilder] = None,
) -> str:
    if not use_few_shot:
        return ""
    sub = few_shot_subdir(benchmark)
    path = os.path.join(_DIR, sub, filename)
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.loads(f.read())
    except Exception as exc:
        logger.warning("  Failed to load few-shot %s/%s: %s", sub, filename, exc)
        return ""
    raw_in = payload.get("input", {})
    inp = build_input(raw_in) if build_input else raw_in
    out = payload.get("output", {})
    ib, ob = json.dumps(inp, ensure_ascii=False, indent=2), json.dumps(out, ensure_ascii=False, indent=2)
    return f"## Few-Shot Example\n\n{input_heading}\n```json\n{ib}\n```\n\n{output_heading}\n```json\n{ob}\n```\n"
