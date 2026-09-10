"""
Evaluation module — comparison logic identical to SpreadsheetBench/evaluation/evaluation.py.
Adds RunningMetrics for real-time pipeline tracking.

SheetCopilot on Linux (openpyxl pipeline): default **relaxed** evaluation compares
cached cell values, matches sheets by content (order-independent), treats pivot
requirements as “rendered table equivalence” (slide + row reorder), and only
enforces chart title/legend when charts are checked — not chart_type/series/axes.
Set environment variable SHEETCOPILOT_STRICT_EVAL=1 to restore index-based
workbook comparison and native pivot/chart object checks (closer to Windows Excel).
"""

import datetime
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openpyxl
import yaml

logger = logging.getLogger(__name__)


def sheetcopilot_use_strict_eval() -> bool:
    return os.environ.get("SHEETCOPILOT_STRICT_EVAL", "").strip().lower() in ("1", "true", "yes", "on")


# ═══════════════════════════════════════════════════════════════════════
#  Value comparison — identical to SpreadsheetBench evaluation.py
# ═══════════════════════════════════════════════════════════════════════

def datetime_to_float(dt):
    """Convert datetime to Excel serial date number."""
    return (dt - datetime.datetime(1899, 12, 30)).days + \
           (dt - datetime.datetime(1899, 12, 30)).seconds / 86400.0


def transform_value(v):
    """Normalize cell value for comparison (round floats to 2 decimals, handle datetime)."""
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, datetime.time):
        return str(v)[:-3]
    if isinstance(v, datetime.datetime):
        return round(datetime_to_float(v), 0)
    if isinstance(v, str):
        try:
            return round(float(v), 2)
        except ValueError:
            pass
    return v


def compare_cell_value(v1, v2):
    """Compare two cell values after normalization. Exact type + value match."""
    v1, v2 = transform_value(v1), transform_value(v2)
    if (v1 == "" and v2 is None) or (v1 is None and v2 == ""):
        return True
    if (v1 == "" and v2 == "") or (v1 is None and v2 is None):
        return True
    if type(v1) != type(v2):
        return False
    return v1 == v2


# ═══════════════════════════════════════════════════════════════════════
#  Cell range helpers — identical to SpreadsheetBench evaluation.py
# ═══════════════════════════════════════════════════════════════════════

def col_name2num(name):
    num = 0
    for c in name:
        num = num * 26 + (ord(c) - ord('A') + 1)
    return num


def col_num2name(n):
    name = ''
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        name = chr(65 + remainder) + name
    return name


def parse_cell_range(range_str):
    """Parse 'A1:AB12' → ((start_col, start_row), (end_col, end_row))."""
    start_cell, end_cell = range_str.split(':')
    start_col = ''.join(c for c in start_cell if not c.isdigit())
    start_row = ''.join(c for c in start_cell if c.isdigit())
    end_col = ''.join(c for c in end_cell if not c.isdigit())
    end_row = ''.join(c for c in end_cell if c.isdigit())
    return (col_name2num(start_col), int(start_row)), (col_name2num(end_col), int(end_row))


def generate_cell_names(range_str):
    """Generate all cell names in a range, e.g. 'A1:B3' → ['A1','A2','A3','B1','B2','B3']."""
    if ':' not in range_str:
        return [range_str]
    (sc, sr), (ec, er) = parse_cell_range(range_str)
    columns = [col_num2name(i) for i in range(sc, ec + 1)]
    return [f"{col}{row}" for col in columns for row in range(sr, er + 1)]


def range_shape(range_str):
    """Return (height, width) for a cell range."""
    (sc, sr), (ec, er) = parse_cell_range(range_str)
    return er - sr + 1, ec - sc + 1


def extract_matrix(ws, start_row, start_col, height, width):
    """Extract a rectangular matrix from a worksheet."""
    return [
        [ws.cell(row=r, column=c).value for c in range(start_col, start_col + width)]
        for r in range(start_row, start_row + height)
    ]


def extract_matrix_from_range(ws, cell_range):
    """Extract matrix using an Excel range string."""
    (sc, sr), _ = parse_cell_range(cell_range)
    height, width = range_shape(cell_range)
    return extract_matrix(ws, sr, sc, height, width)


def normalize_value_key(v):
    """Convert values into stable comparable keys for table-level matching."""
    v = transform_value(v)
    if v == "" or v is None:
        return ("empty", "")
    if isinstance(v, str):
        return ("str", v.strip())
    return (type(v).__name__, v)


def matrices_equal(matrix_gt, matrix_proc):
    """Exact matrix compare using cell-level normalization."""
    if len(matrix_gt) != len(matrix_proc):
        return False
    if matrix_gt and matrix_proc and len(matrix_gt[0]) != len(matrix_proc[0]):
        return False
    for gt_row, proc_row in zip(matrix_gt, matrix_proc):
        for gt_cell, proc_cell in zip(gt_row, proc_row):
            if not compare_cell_value(gt_cell, proc_cell):
                return False
    return True


def canonicalize_table(matrix):
    """
    Build a stable table representation that tolerates row and column reorder.

    This is an iterative row/column sorting canonicalization that works well for
    spreadsheet-style answer blocks.
    """
    canonical = tuple(tuple(normalize_value_key(cell) for cell in row) for row in matrix)
    if not canonical:
        return canonical

    for _ in range(8):
        previous = canonical
        canonical = tuple(sorted(canonical))
        columns = list(zip(*canonical)) if canonical and canonical[0] else []
        columns = sorted(tuple(col) for col in columns)
        canonical = tuple(zip(*columns)) if columns else canonical
        if canonical == previous:
            break
    return canonical


def tables_match_with_reordering(matrix_gt, matrix_proc):
    """Allow row reorder and column reorder when comparing answer blocks."""
    if len(matrix_gt) != len(matrix_proc):
        return False
    if matrix_gt and matrix_proc and len(matrix_gt[0]) != len(matrix_proc[0]):
        return False
    return canonicalize_table(matrix_gt) == canonicalize_table(matrix_proc)


def search_matching_region(ws_gt, ws_proc, cell_range):
    """
    Search the processed worksheet for any same-sized region matching GT.

    Matching first tries exact cell-by-cell equality, then relaxed table-level
    equality that allows row and column reorder.
    """
    gt_matrix = extract_matrix_from_range(ws_gt, cell_range)
    height, width = range_shape(cell_range)
    max_start_row = ws_proc.max_row - height + 1
    max_start_col = ws_proc.max_column - width + 1

    if max_start_row < 1 or max_start_col < 1:
        return False, "worksheet smaller than expected answer block"

    target_signature = canonicalize_table(gt_matrix)
    for start_row in range(1, max_start_row + 1):
        for start_col in range(1, max_start_col + 1):
            candidate = extract_matrix(ws_proc, start_row, start_col, height, width)
            if matrices_equal(gt_matrix, candidate):
                return True, ""
            if canonicalize_table(candidate) == target_signature:
                return True, ""
    return False, "no matching region found in worksheet"


# ═══════════════════════════════════════════════════════════════════════
#  Workbook comparison — identical to SpreadsheetBench evaluation.py
# ═══════════════════════════════════════════════════════════════════════

def cell_level_compare(wb_gt, wb_proc, sheet_name, cell_range):
    """Compare a GT answer block against the output sheet with region tolerance."""
    if sheet_name not in wb_proc:
        return False, "worksheet not found"
    ws_gt, ws_proc = wb_gt[sheet_name], wb_proc[sheet_name]

    # First try the original exact location.
    gt_matrix = extract_matrix_from_range(ws_gt, cell_range)
    proc_matrix = extract_matrix_from_range(ws_proc, cell_range)
    if matrices_equal(gt_matrix, proc_matrix):
        return True, ""
    if tables_match_with_reordering(gt_matrix, proc_matrix):
        return True, ""

    # If exact location fails, slide over the full worksheet.
    return search_matching_region(ws_gt, ws_proc, cell_range)


def compare_workbooks(gt_file, proc_file, answer_position):
    """Compare output workbook against ground-truth at answer_position. Returns (passed, msg)."""
    if not os.path.exists(proc_file):
        return False, "File not exist"
    try:
        wb_gt = openpyxl.load_workbook(gt_file, data_only=True)
        wb_proc = openpyxl.load_workbook(proc_file, data_only=True)
    except Exception as e:
        return False, str(e)

    results = []
    for sheet_cell_range in answer_position.split(','):
        if '!' in sheet_cell_range:
            sheet_name, cell_range = sheet_cell_range.split('!')
            sheet_name = sheet_name.strip("'\"")
        else:
            sheet_name = wb_gt.sheetnames[0]
            cell_range = sheet_cell_range
        cell_range = cell_range.strip("'\"")
        result, msg = cell_level_compare(wb_gt, wb_proc, sheet_name, cell_range)
        results.append(result)

    wb_gt.close()
    wb_proc.close()
    return all(results), ""


# ═══════════════════════════════════════════════════════════════════════
#  SheetCopilot evaluation helpers
# ═══════════════════════════════════════════════════════════════════════

def _normalize_formula_string(value):
    if not isinstance(value, str):
        return value
    return value.replace('"', "'").strip()


def _compare_sheetcopilot_value(v1, v2):
    if isinstance(v1, str) and v1.startswith("="):
        return _normalize_formula_string(v1) == _normalize_formula_string(v2)
    if isinstance(v2, str) and v2.startswith("="):
        return _normalize_formula_string(v1) == _normalize_formula_string(v2)
    return compare_cell_value(v1, v2)


def _hyperlink_target(cell):
    if cell.hyperlink is None:
        return None
    return cell.hyperlink.target or cell.hyperlink.location or ""


def _extract_chart_title(chart):
    title = getattr(chart, "title", None)
    if title is None:
        return None
    text = getattr(title, "tx", None)
    if text is None:
        return None

    str_ref = getattr(text, "strRef", None)
    if str_ref is not None:
        f = getattr(getattr(str_ref, "f", None), "text", None) or getattr(str_ref, "f", None)
        if isinstance(f, str) and f:
            return f

    rich = getattr(text, "rich", None)
    if rich is None:
        return None

    parts = []
    for paragraph in getattr(rich, "p", []) or []:
        for run in getattr(paragraph, "r", []) or []:
            t = getattr(run, "t", None)
            if t is None:
                continue
            if isinstance(t, str):
                chunk = t
            else:
                chunk = getattr(t, "value", None)
            if chunk:
                parts.append(str(chunk))
    out = "".join(parts).strip()
    return out or None


def _chart_type_name(chart):
    return getattr(chart, "type", None) or chart.__class__.__name__


def _chart_has_legend(chart):
    return getattr(chart, "legend", None) is not None


def _chart_axes_count(chart):
    return sum(int(getattr(chart, axis_name, None) is not None) for axis_name in ("x_axis", "y_axis", "z_axis"))


def _chart_series_count(chart):
    return len(getattr(chart, "ser", []) or [])


def _chart_has_data_labels(chart):
    if getattr(chart, "dLbls", None) is not None:
        return True
    return any(getattr(series, "dLbls", None) is not None for series in getattr(chart, "ser", []) or [])


def _chart_trendline_count(chart):
    return sum(int(getattr(series, "trendline", None) is not None) for series in getattr(chart, "ser", []) or [])


def _compare_cells_sheetcopilot(ws_gt, ws_proc, cell_cfg):
    max_row = max(ws_gt.max_row, ws_proc.max_row)
    max_col = max(ws_gt.max_column, ws_proc.max_column)
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            gt_cell = ws_gt.cell(row=row, column=col)
            proc_cell = ws_proc.cell(row=row, column=col)
            if cell_cfg.get("values") and not _compare_sheetcopilot_value(gt_cell.value, proc_cell.value):
                return False, f"cell value mismatch at {gt_cell.coordinate}"
            if cell_cfg.get("formatting") and gt_cell.style_id != proc_cell.style_id:
                return False, f"cell formatting mismatch at {gt_cell.coordinate}"
            if cell_cfg.get("hyperlink") and _hyperlink_target(gt_cell) != _hyperlink_target(proc_cell):
                return False, f"cell hyperlink mismatch at {gt_cell.coordinate}"
    return True, ""


def _compare_charts_sheetcopilot(ws_gt, ws_proc, chart_cfg):
    gt_charts = list(getattr(ws_gt, "_charts", []) or [])
    proc_charts = list(getattr(ws_proc, "_charts", []) or [])
    if len(gt_charts) != len(proc_charts):
        return False, "chart count mismatch"

    for idx, (gt_chart, proc_chart) in enumerate(zip(gt_charts, proc_charts), start=1):
        if chart_cfg.get("chart_type") and _chart_type_name(gt_chart) != _chart_type_name(proc_chart):
            return False, f"chart type mismatch at chart #{idx}"
        if chart_cfg.get("title") and _extract_chart_title(gt_chart) != _extract_chart_title(proc_chart):
            return False, f"chart title mismatch at chart #{idx}"
        if chart_cfg.get("legend") and _chart_has_legend(gt_chart) != _chart_has_legend(proc_chart):
            return False, f"chart legend mismatch at chart #{idx}"
        if chart_cfg.get("axes") and _chart_axes_count(gt_chart) != _chart_axes_count(proc_chart):
            return False, f"chart axes mismatch at chart #{idx}"
        if chart_cfg.get("series") and _chart_series_count(gt_chart) != _chart_series_count(proc_chart):
            return False, f"chart series mismatch at chart #{idx}"
        if chart_cfg.get("data_labels") and _chart_has_data_labels(gt_chart) != _chart_has_data_labels(proc_chart):
            return False, f"chart data labels mismatch at chart #{idx}"
        if chart_cfg.get("trendlines") and _chart_trendline_count(gt_chart) != _chart_trendline_count(proc_chart):
            return False, f"chart trendline mismatch at chart #{idx}"
    return True, ""


def _compare_filters_sheetcopilot(ws_gt, ws_proc, filter_cfg):
    gt_filter = ws_gt.auto_filter
    proc_filter = ws_proc.auto_filter
    if filter_cfg.get("source") and getattr(gt_filter, "ref", None) != getattr(proc_filter, "ref", None):
        return False, "filter source mismatch"
    if filter_cfg.get("filters"):
        gt_columns = len(getattr(gt_filter, "filterColumn", []) or [])
        proc_columns = len(getattr(proc_filter, "filterColumn", []) or [])
        if gt_columns != proc_columns:
            return False, "filter column mismatch"
    return True, ""


def _compare_format_conditions_sheetcopilot(ws_gt, ws_proc, condition_cfg):
    gt_cf = ws_gt.conditional_formatting
    proc_cf = ws_proc.conditional_formatting
    gt_ranges = list(gt_cf)
    proc_ranges = list(proc_cf)
    if len(gt_ranges) != len(proc_ranges):
        return False, "conditional formatting range mismatch"
    for idx, (gt_range, proc_range) in enumerate(zip(gt_ranges, proc_ranges), start=1):
        gt_rules = gt_cf[gt_range]
        proc_rules = proc_cf[proc_range]
        if len(gt_rules) != len(proc_rules):
            return False, f"conditional formatting rule count mismatch at range #{idx}"
        for rule_idx, (gt_rule, proc_rule) in enumerate(zip(gt_rules, proc_rules), start=1):
            for attr in ("type", "operator", "formula"):
                yaml_key = "formula1" if attr == "formula" else attr
                if condition_cfg.get(yaml_key):
                    gt_value = getattr(gt_rule, attr, None)
                    proc_value = getattr(proc_rule, attr, None)
                    if gt_value != proc_value:
                        return False, f"conditional formatting mismatch at range #{idx} rule #{rule_idx}"
    return True, ""


def _compare_pivots_sheetcopilot(ws_gt, ws_proc, pivot_cfg):
    gt_pivots = list(getattr(ws_gt, "_pivots", []) or [])
    proc_pivots = list(getattr(ws_proc, "_pivots", []) or [])
    if len(gt_pivots) != len(proc_pivots):
        return False, "pivot table count mismatch"
    if not gt_pivots:
        return True, ""

    for idx, (gt_pivot, proc_pivot) in enumerate(zip(gt_pivots, proc_pivots), start=1):
        if pivot_cfg.get("name") and getattr(gt_pivot, "name", None) != getattr(proc_pivot, "name", None):
            return False, f"pivot name mismatch at pivot #{idx}"
        if pivot_cfg.get("source") and getattr(gt_pivot, "cache", None) != getattr(proc_pivot, "cache", None):
            return False, f"pivot source mismatch at pivot #{idx}"
        if pivot_cfg.get("rows") and len(getattr(gt_pivot, "rowFields", []) or []) != len(getattr(proc_pivot, "rowFields", []) or []):
            return False, f"pivot row field mismatch at pivot #{idx}"
        if pivot_cfg.get("columns") and len(getattr(gt_pivot, "colFields", []) or []) != len(getattr(proc_pivot, "colFields", []) or []):
            return False, f"pivot column field mismatch at pivot #{idx}"
        if pivot_cfg.get("filters") and len(getattr(gt_pivot, "pageFields", []) or []) != len(getattr(proc_pivot, "pageFields", []) or []):
            return False, f"pivot filter mismatch at pivot #{idx}"
        if pivot_cfg.get("values") and len(getattr(gt_pivot, "dataFields", []) or []) != len(getattr(proc_pivot, "dataFields", []) or []):
            return False, f"pivot value field mismatch at pivot #{idx}"
    return True, ""


def _normalize_fuzzy_title(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _chart_titles_match_relaxed(gt_chart, proc_chart) -> bool:
    return _normalize_fuzzy_title(_extract_chart_title(gt_chart)) == _normalize_fuzzy_title(
        _extract_chart_title(proc_chart)
    )


def _first_pivot_anchor_range(ws) -> Optional[str]:
    pivots = getattr(ws, "_pivots", None) or []
    if not pivots:
        return None
    loc = getattr(pivots[0], "location", None)
    if loc is None:
        return None
    ref = getattr(loc, "ref", None)
    if ref is None:
        ref = getattr(loc, "coord", None)
    if ref is None:
        ref = str(loc)
    return str(ref) if ref else None


def _trim_grand_total_row(matrix: List[List]) -> List[List]:
    if not matrix or len(matrix) < 2:
        return matrix
    label = matrix[-1][0] if matrix[-1] else None
    if isinstance(label, str):
        low = label.lower()
        if "total" in low or "总计" in label or "合计" in label:
            return matrix[:-1]
    return matrix


def _pivot_body_rows_for_match(matrix: List[List]) -> List[List]:
    """
    GT pivot render often uses locale headers (e.g. 行标签 / 计数项) while openpyxl
    pipelines use English (Web Site / Count). Drop the header row so we only match
    category + value rows.
    """
    if not matrix or len(matrix) < 2:
        return matrix
    return matrix[1:]


def _search_matrix_anywhere(ws_proc, gt_matrix: List[List]) -> Tuple[bool, str]:
    if not gt_matrix or not gt_matrix[0]:
        return True, ""
    height, width = len(gt_matrix), len(gt_matrix[0])
    max_start_row = ws_proc.max_row - height + 1
    max_start_col = ws_proc.max_column - width + 1
    if max_start_row < 1 or max_start_col < 1:
        return False, "worksheet smaller than pivot-equivalent block"
    target = canonicalize_table(gt_matrix)
    for start_row in range(1, max_start_row + 1):
        for start_col in range(1, max_start_col + 1):
            candidate = extract_matrix(ws_proc, start_row, start_col, height, width)
            if matrices_equal(gt_matrix, candidate):
                return True, ""
            if canonicalize_table(candidate) == target:
                return True, ""
    return False, "no pivot-equivalent value block found"


def _search_pivot_body_anywhere(ws_proc, gt_body: List[List]) -> Tuple[bool, str]:
    """
    Like _search_matrix_anywhere but gt_body is pivot data rows only (no header).
    Matches either a same-sized block or a block with one extra header row above.
    """
    if not gt_body or not gt_body[0]:
        return True, ""
    h, w = len(gt_body), len(gt_body[0])
    target = canonicalize_table(gt_body)
    for start_row in range(1, ws_proc.max_row + 1):
        for start_col in range(1, ws_proc.max_column + 1):
            if start_row + h - 1 <= ws_proc.max_row and start_col + w - 1 <= ws_proc.max_column:
                cand = extract_matrix(ws_proc, start_row, start_col, h, w)
                if matrices_equal(gt_body, cand) or canonicalize_table(cand) == target:
                    return True, ""
            if start_row + h <= ws_proc.max_row and start_col + w - 1 <= ws_proc.max_column:
                block = extract_matrix(ws_proc, start_row, start_col, h + 1, w)
                tail = block[1:]
                if matrices_equal(gt_body, tail) or canonicalize_table(tail) == target:
                    return True, ""
    return False, "no pivot-equivalent value block found"


def _compare_pivot_surrogate_openpyxl(ws_gt_meta, ws_gt_vals, ws_proc_vals, pivot_cfg) -> Tuple[bool, str]:
    if not any(pivot_cfg.values()):
        return True, ""
    ref = _first_pivot_anchor_range(ws_gt_meta)
    if ref:
        gt_matrix = extract_matrix_from_range(ws_gt_vals, ref)
    else:
        logger.warning(
            "SheetCopilot relaxed eval: GT sheet %r has no openpyxl _pivots anchor; "
            "using top-left A1:J40 heuristic for pivot-equivalent block",
            ws_gt_meta.title,
        )
        n_row = max(1, min(40, ws_gt_vals.max_row or 1))
        n_col = max(1, min(10, ws_gt_vals.max_column or 1))
        gt_matrix = extract_matrix(ws_gt_vals, 1, 1, n_row, n_col)
        while gt_matrix and all(v is None or v == "" for v in gt_matrix[-1]):
            gt_matrix = gt_matrix[:-1]
        h = min(len(gt_matrix), 25)
        w = min(len(gt_matrix[0]) if gt_matrix else 0, 6)
        if h < 2 or w < 2:
            return False, "cannot infer pivot region from GT"
        gt_matrix = [row[:w] for row in gt_matrix[:h]]

    gt_matrix = _trim_grand_total_row(gt_matrix)
    gt_body = _pivot_body_rows_for_match(gt_matrix)
    return _search_pivot_body_anywhere(ws_proc_vals, gt_body)


def _effective_chart_cfg_relaxed(chart_cfg: dict) -> dict:
    out = dict(chart_cfg or {})
    for k in ("chart_type", "series", "axes", "name", "trendlines", "data_labels"):
        out[k] = False
    return out


def _compare_charts_sheetcopilot_relaxed(ws_gt, ws_proc, chart_cfg: dict) -> Tuple[bool, str]:
    gt_charts = list(getattr(ws_gt, "_charts", []) or [])
    proc_charts = list(getattr(ws_proc, "_charts", []) or [])
    if len(gt_charts) != len(proc_charts):
        return False, "chart count mismatch"
    for idx, (gt_chart, proc_chart) in enumerate(zip(gt_charts, proc_charts), start=1):
        if chart_cfg.get("title") and not _chart_titles_match_relaxed(gt_chart, proc_chart):
            return False, f"chart title mismatch at chart #{idx}"
        if chart_cfg.get("legend") and _chart_has_legend(gt_chart) != _chart_has_legend(proc_chart):
            return False, f"chart legend mismatch at chart #{idx}"
    return True, ""


def _compare_cell_openpyxl_relaxed(gt_m, gt_v, proc_m, proc_v) -> bool:
    """
    Compare one cell when GT/output may mix formulas vs cached values (openpyxl save).
    Prefer evaluated values when both exist; fall back to formula–formula text match.
    """
    g_m, g_v = gt_m.value, gt_v.value
    p_m, p_v = proc_m.value, proc_v.value
    g_f = isinstance(g_m, str) and g_m.startswith("=")
    p_f = isinstance(p_m, str) and p_m.startswith("=")
    if g_f and p_f:
        return _compare_sheetcopilot_value(g_m, p_m)
    if g_f and not p_f and p_v is not None:
        return compare_cell_value(g_v, p_v)
    if p_f and not g_f and g_v is not None:
        return compare_cell_value(g_v, p_v)
    if g_v is not None or p_v is not None:
        return compare_cell_value(g_v, p_v)
    if g_f or p_f:
        return _compare_sheetcopilot_value(g_m, p_m)
    return compare_cell_value(g_m, p_m)


def _compare_cells_sheetcopilot_openpyxl_relaxed(ws_gt_m, ws_gt_v, ws_proc_m, ws_proc_v, cell_cfg) -> Tuple[bool, str]:
    if not any((cell_cfg or {}).values()):
        return True, ""
    max_row = max(ws_gt_m.max_row, ws_proc_m.max_row)
    max_col = max(ws_gt_m.max_column, ws_proc_m.max_column)
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            gt_m = ws_gt_m.cell(row=row, column=col)
            gt_v = ws_gt_v.cell(row=row, column=col)
            proc_m = ws_proc_m.cell(row=row, column=col)
            proc_v = ws_proc_v.cell(row=row, column=col)
            if cell_cfg.get("values") and not _compare_cell_openpyxl_relaxed(gt_m, gt_v, proc_m, proc_v):
                return False, f"cell value mismatch at {gt_m.coordinate}"
            if cell_cfg.get("formatting") and gt_m.style_id != proc_m.style_id:
                return False, f"cell formatting mismatch at {gt_m.coordinate}"
            if cell_cfg.get("hyperlink") and _hyperlink_target(gt_m) != _hyperlink_target(proc_m):
                return False, f"cell hyperlink mismatch at {gt_m.coordinate}"
    return True, ""


def _compare_sheet_pair_openpyxl_relaxed(
    ws_gt_meta,
    ws_gt_vals,
    ws_proc_meta,
    ws_proc_vals,
    cfg: dict,
) -> Tuple[bool, str]:
    ok, msg = _compare_cells_sheetcopilot_openpyxl_relaxed(
        ws_gt_meta, ws_gt_vals, ws_proc_meta, ws_proc_vals, cfg.get("cells", {}),
    )
    if not ok:
        return False, msg

    chart_cfg = cfg.get("charts", {}) or {}
    if any(chart_cfg.values()):
        eff = _effective_chart_cfg_relaxed(chart_cfg)
        if any(eff.values()):
            ok, msg = _compare_charts_sheetcopilot_relaxed(ws_gt_meta, ws_proc_meta, eff)
            if not ok:
                return False, msg

    if any(cfg.get("filters", {}).values()):
        ok, msg = _compare_filters_sheetcopilot(ws_gt_meta, ws_proc_meta, cfg.get("filters", {}))
        if not ok:
            return False, msg

    if any(cfg.get("format_conditions", {}).values()):
        ok, msg = _compare_format_conditions_sheetcopilot(ws_gt_meta, ws_proc_meta, cfg.get("format_conditions", {}))
        if not ok:
            return False, msg

    if any(cfg.get("pivot_tables", {}).values()):
        ok, msg = _compare_pivot_surrogate_openpyxl(
            ws_gt_meta, ws_gt_vals, ws_proc_vals, cfg.get("pivot_tables", {})
        )
        if not ok:
            return False, msg

    if cfg.get("view", {}).get("freeze_pane") and ws_gt_meta.freeze_panes != ws_proc_meta.freeze_panes:
        return False, "freeze pane mismatch"

    return True, ""


def compare_sheetcopilot_workbooks_strict(gt_file, proc_file, check_board) -> Tuple[bool, str]:
    if not os.path.exists(proc_file):
        return False, "result workbook not found"

    try:
        wb_gt = openpyxl.load_workbook(gt_file, data_only=False)
        wb_proc = openpyxl.load_workbook(proc_file, data_only=False)
    except Exception as exc:
        return False, str(exc)

    try:
        if wb_gt.worksheets and wb_proc.worksheets and len(wb_gt.worksheets) != len(wb_proc.worksheets):
            return False, "worksheet count mismatch"

        for sheet_ref in sorted(check_board.keys(), key=lambda item: int(item)):
            cfg = check_board[sheet_ref] or {}
            sheet_idx = int(sheet_ref) - 1
            if sheet_idx >= len(wb_gt.worksheets) or sheet_idx >= len(wb_proc.worksheets):
                return False, f"worksheet index out of range: {sheet_ref}"

            ws_gt = wb_gt.worksheets[sheet_idx]
            ws_proc = wb_proc.worksheets[sheet_idx]

            ok, msg = _compare_cells_sheetcopilot(ws_gt, ws_proc, cfg.get("cells", {}))
            if not ok:
                return False, msg

            if any(cfg.get("charts", {}).values()):
                ok, msg = _compare_charts_sheetcopilot(ws_gt, ws_proc, cfg.get("charts", {}))
                if not ok:
                    return False, msg

            if any(cfg.get("filters", {}).values()):
                ok, msg = _compare_filters_sheetcopilot(ws_gt, ws_proc, cfg.get("filters", {}))
                if not ok:
                    return False, msg

            if any(cfg.get("format_conditions", {}).values()):
                ok, msg = _compare_format_conditions_sheetcopilot(ws_gt, ws_proc, cfg.get("format_conditions", {}))
                if not ok:
                    return False, msg

            if any(cfg.get("pivot_tables", {}).values()):
                ok, msg = _compare_pivots_sheetcopilot(ws_gt, ws_proc, cfg.get("pivot_tables", {}))
                if not ok:
                    return False, msg

            if cfg.get("view", {}).get("freeze_pane") and ws_gt.freeze_panes != ws_proc.freeze_panes:
                return False, "freeze pane mismatch"

        return True, ""
    finally:
        wb_gt.close()
        wb_proc.close()


def compare_sheetcopilot_workbooks_relaxed(gt_file, proc_file, check_board) -> Tuple[bool, str]:
    if not os.path.exists(proc_file):
        return False, "result workbook not found"

    try:
        wb_gt_meta = openpyxl.load_workbook(gt_file, data_only=False)
        wb_gt_vals = openpyxl.load_workbook(gt_file, data_only=True)
        wb_proc_meta = openpyxl.load_workbook(proc_file, data_only=False)
        wb_proc_vals = openpyxl.load_workbook(proc_file, data_only=True)
    except Exception as exc:
        return False, str(exc)

    try:
        used_proc: set = set()
        for sheet_ref in sorted(check_board.keys(), key=lambda item: int(item)):
            cfg = check_board[sheet_ref] or {}
            sheet_idx = int(sheet_ref) - 1
            if sheet_idx >= len(wb_gt_meta.worksheets):
                return False, f"GT worksheet index out of range: {sheet_ref}"

            ws_gt_m = wb_gt_meta.worksheets[sheet_idx]
            ws_gt_v = wb_gt_vals.worksheets[sheet_idx]

            found: Optional[int] = None
            for i, _ws in enumerate(wb_proc_meta.worksheets):
                if i in used_proc:
                    continue
                ok, _msg = _compare_sheet_pair_openpyxl_relaxed(
                    ws_gt_m,
                    ws_gt_v,
                    wb_proc_meta.worksheets[i],
                    wb_proc_vals.worksheets[i],
                    cfg,
                )
                if ok:
                    found = i
                    break

            if found is None:
                return False, f"no output sheet matches check_board[{sheet_ref}] (GT sheet {ws_gt_m.title!r})"

            used_proc.add(found)

        return True, ""
    finally:
        wb_gt_meta.close()
        wb_gt_vals.close()
        wb_proc_meta.close()
        wb_proc_vals.close()


def compare_sheetcopilot_workbooks(gt_file, proc_file, check_board, relaxed: Optional[bool] = None) -> Tuple[bool, str]:
    if relaxed is None:
        relaxed = not sheetcopilot_use_strict_eval()
    if relaxed:
        return compare_sheetcopilot_workbooks_relaxed(gt_file, proc_file, check_board)
    return compare_sheetcopilot_workbooks_strict(gt_file, proc_file, check_board)


# ═══════════════════════════════════════════════════════════════════════
#  Sample evaluation
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SampleEval:
    sample_id: str
    instruction_type: str = ""
    benchmark: str = "spreadsheetbench"
    exec_at_1: int = 0
    pass_at_1: int = 0
    test_case_results: List[int] = field(default_factory=list)
    matched_gt: str = ""
    message: str = ""
    # SpreadsheetBench only: hard = 1 iff all 3 test cases pass; soft = pass rate over those 3.
    # SheetCopilot: leave hard_restriction/soft_restriction as None; use exec_at_1 / pass_at_1 only.
    hard_restriction: Optional[int] = None
    soft_restriction: Optional[float] = None


def evaluate_spreadsheetbench_sample(sample, output_dir, data_dir):
    """Each sample has 3 input/output variants (1_, 2_, 3_); hard/soft follow SpreadsheetBench eval."""
    sample_id = str(sample["id"])
    spreadsheet_dir = os.path.join(data_dir, sample["spreadsheet_path"])
    answer_position = sample["answer_position"]

    test_case_results = []
    exec_success = 1
    for idx in range(1, 4):
        gt_path = os.path.join(spreadsheet_dir, f"{idx}_{sample_id}_answer.xlsx")
        proc_path = os.path.join(output_dir, sample_id, f"{idx}_{sample_id}_output.xlsx")
        if not os.path.exists(proc_path):
            exec_success = 0
        try:
            passed, _ = compare_workbooks(gt_path, proc_path, answer_position)
        except Exception:
            passed = False
        test_case_results.append(int(passed))

    n = len(test_case_results)
    soft = (sum(test_case_results) / n) if n else 0.0
    hard = 0 if (not test_case_results or 0 in test_case_results) else 1
    return SampleEval(
        sample_id=sample_id,
        instruction_type=sample.get("instruction_type", ""),
        benchmark="spreadsheetbench",
        exec_at_1=exec_success,
        pass_at_1=hard,
        test_case_results=test_case_results,
        hard_restriction=hard,
        soft_restriction=soft,
    )


def evaluate_sheetcopilot_sample(sample, output_dir):
    """
    One task → one output workbook vs ground truth (YAML check_board).
    Unlike SpreadsheetBench, there is **no** 3-way test-case loop; Exec@1 / Pass@1 are per-sample 0/1.
    """
    sample_id = str(sample["id"])
    sample_output_dir = os.path.join(output_dir, sample_id)
    output_path = os.path.join(sample_output_dir, f"{sample_id}_output.xlsx")
    log_path = os.path.join(sample_output_dir, "sample_io_log.json")

    success = False
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
            success = bool(payload.get("success"))
        except (OSError, json.JSONDecodeError):
            success = False

    exec_at_1 = int(success and os.path.exists(output_path))
    if not os.path.isdir(sample["gt_dir"]):
        return SampleEval(
            sample_id=sample_id,
            instruction_type=sample.get("instruction_type", ""),
            benchmark="sheetcopilot",
            exec_at_1=exec_at_1,
            pass_at_1=0,
            message="missing gt directory",
        )

    for gt_file in sorted(os.listdir(sample["gt_dir"])):
        if not gt_file.endswith(".xlsx") or "$" in gt_file:
            continue
        gt_path = os.path.join(sample["gt_dir"], gt_file)
        check_path = gt_path.replace(".xlsx", "_check.yaml")
        if not os.path.exists(check_path):
            continue
        with open(check_path, "r", encoding="utf-8") as fp:
            check_board = yaml.load(fp, Loader=yaml.Loader)
        passed, message = compare_sheetcopilot_workbooks(gt_path, output_path, check_board["check_board"])
        if passed:
            return SampleEval(
                sample_id=sample_id,
                instruction_type=sample.get("instruction_type", ""),
                benchmark="sheetcopilot",
                exec_at_1=exec_at_1,
                pass_at_1=int(exec_at_1 == 1),
                matched_gt=gt_file,
            )

    return SampleEval(
        sample_id=sample_id,
        instruction_type=sample.get("instruction_type", ""),
        benchmark="sheetcopilot",
        exec_at_1=exec_at_1,
        pass_at_1=0,
        message="no ground truth matched",
    )


def evaluate_sample(sample, output_dir, data_dir=None):
    benchmark = sample.get("benchmark", "spreadsheetbench")
    if benchmark == "sheetcopilot":
        return evaluate_sheetcopilot_sample(sample, output_dir)
    return evaluate_spreadsheetbench_sample(sample, output_dir, data_dir)


# ═══════════════════════════════════════════════════════════════════════
#  Running metrics — real-time accumulator for pipeline
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RunningMetrics:
    """
    SheetCopilot: aggregate Exec@1 and Pass@1 over samples (each sample = single output vs GT, not 3 cases).
    SpreadsheetBench: aggregate hard_restriction / soft_restriction (each sample = 3 input variants vs GT).
    """

    benchmark: str = "spreadsheetbench"
    total_samples: int = 0
    exec_sum: int = 0
    pass_sum: int = 0
    hard_sum: int = 0
    soft_sum: float = 0.0
    type_stats: Dict[str, Dict[str, list]] = field(default_factory=dict)

    @property
    def exec_at_1(self):
        return self.exec_sum / self.total_samples if self.total_samples else 0.0

    @property
    def pass_at_1(self):
        return self.pass_sum / self.total_samples if self.total_samples else 0.0

    @property
    def hard_restriction_rate(self):
        return self.hard_sum / self.total_samples if self.total_samples else 0.0

    @property
    def soft_restriction_rate(self):
        return self.soft_sum / self.total_samples if self.total_samples else 0.0

    def update(self, ev: SampleEval):
        self.total_samples += 1
        bench = ev.benchmark or self.benchmark
        if bench == "sheetcopilot":
            self.exec_sum += ev.exec_at_1
            self.pass_sum += ev.pass_at_1
        else:
            if ev.hard_restriction is not None:
                self.hard_sum += ev.hard_restriction
            if ev.soft_restriction is not None:
                self.soft_sum += ev.soft_restriction

        itype = ev.instruction_type or "unknown"
        if itype not in self.type_stats:
            self.type_stats[itype] = {"exec": [], "pass": [], "hard": [], "soft": []}
        ts = self.type_stats[itype]
        if bench == "sheetcopilot":
            ts["exec"].append(ev.exec_at_1)
            ts["pass"].append(ev.pass_at_1)
        else:
            if ev.hard_restriction is not None:
                ts["hard"].append(ev.hard_restriction)
            if ev.soft_restriction is not None:
                ts["soft"].append(ev.soft_restriction)

    def summary(self) -> str:
        lines = ["", "=" * 55, "  EVALUATION RESULTS", "=" * 55, f"  Total samples:        {self.total_samples}"]
        if self.benchmark == "sheetcopilot":
            lines.extend(
                [
                    f"  Exec@1:               {self.exec_at_1:.2%}",
                    f"  Pass@1:               {self.pass_at_1:.2%}",
                    "  (Per sample: one generated workbook vs GT; not SpreadsheetBench’s 3 test cases.)",
                ]
            )
        else:
            lines.extend(
                [
                    f"  Hard restriction:     {self.hard_restriction_rate:.2%}",
                    f"  Soft restriction:     {self.soft_restriction_rate:.2%}",
                    "  (Hard = all 3 test cases pass; Soft = mean pass rate over 3 cases)",
                ]
            )
        if self.type_stats:
            lines.append("")
            lines.append("  By instruction type:")
            for itype, stats in self.type_stats.items():
                if self.benchmark == "sheetcopilot":
                    ev, pv = stats["exec"], stats["pass"]
                    a, b = (
                        sum(ev) / len(ev) if ev else 0.0,
                        sum(pv) / len(pv) if pv else 0.0,
                    )
                    lines.append(f"    {itype}: Exec@1={a:.2%}, Pass@1={b:.2%}, n={len(ev)}")
                else:
                    hv, sv = stats["hard"], stats["soft"]
                    a, b = (
                        sum(hv) / len(hv) if hv else 0.0,
                        sum(sv) / len(sv) if sv else 0.0,
                    )
                    lines.append(f"    {itype}: Hard={a:.2%}, Soft={b:.2%}, n={len(hv)}")
        lines.append("=" * 55)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        type_metrics = {}
        for itype, stats in self.type_stats.items():
            if self.benchmark == "sheetcopilot":
                exec_values, pass_values = stats["exec"], stats["pass"]
                type_metrics[itype] = {
                    "count": len(exec_values),
                    "exec_at_1": round(sum(exec_values) / len(exec_values), 4) if exec_values else 0.0,
                    "pass_at_1": round(sum(pass_values) / len(pass_values), 4) if pass_values else 0.0,
                }
            else:
                hv, sv = stats["hard"], stats["soft"]
                type_metrics[itype] = {
                    "count": len(hv),
                    "hard_restriction": round(sum(hv) / len(hv), 4) if hv else 0.0,
                    "soft_restriction": round(sum(sv) / len(sv), 4) if sv else 0.0,
                }
        out = {
            "benchmark": self.benchmark,
            "total_samples": self.total_samples,
            "type_metrics": type_metrics,
        }
        if self.benchmark == "sheetcopilot":
            out["exec_at_1"] = round(self.exec_at_1, 4)
            out["pass_at_1"] = round(self.pass_at_1, 4)
        else:
            out["hard_restriction"] = round(self.hard_restriction_rate, 4)
            out["soft_restriction"] = round(self.soft_restriction_rate, 4)
        return out
