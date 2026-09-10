"""
Code execution tool: extract Python code from LLM response, execute it locally.

Used as a tool in the pipeline — generates code once, then reuses it across
test cases by adapting file paths.
"""

import re
import sys
import os
import traceback
from datetime import datetime
from contextlib import contextmanager, nullcontext
from io import StringIO


def extract_code(response):
    """Extract Python code block from LLM response."""
    match = re.search(r'```python\s*(.*?)```', response, re.DOTALL)
    return match.group(1).strip() if match else response.strip()


def _normalize_safe_cell_value(value):
    """Normalize worksheet reads so mixed string/number/date comparisons are less likely to fail."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%Y %H:%M:%S",
        ):
            try:
                return datetime.strptime(stripped, fmt)
            except ValueError:
                pass

        iso_candidate = stripped.replace("T", " ")
        try:
            return datetime.fromisoformat(iso_candidate)
        except ValueError:
            pass

        numeric_text = stripped.replace(",", "")
        try:
            number = float(numeric_text)
        except ValueError:
            return stripped
        return int(number) if number.is_integer() else number
    return value


@contextmanager
def _safe_openpyxl_value_patch():
    """
    Monkey patch openpyxl cell reads during execution.

    This keeps generated code unchanged while normalizing cell.value reads into
    safer numeric/None values when a sheet mixes numbers and text.
    """
    patched = []
    try:
        from openpyxl.cell.cell import Cell

        for cls in (Cell,):
            original_property = getattr(cls, "value", None)
            if original_property is None or not hasattr(original_property, "fget"):
                continue
            original_getter = original_property.fget
            original_setter = getattr(original_property, "fset", None)

            def safe_getter(self, _getter=original_getter):
                return _normalize_safe_cell_value(_getter(self))

            setattr(cls, "value", property(safe_getter, original_setter))
            patched.append((cls, original_property))

        try:
            from openpyxl.cell.read_only import ReadOnlyCell
        except Exception:
            ReadOnlyCell = None

        if ReadOnlyCell is not None:
            original_property = getattr(ReadOnlyCell, "value", None)
            if original_property is not None and hasattr(original_property, "fget"):
                original_getter = original_property.fget
                original_setter = getattr(original_property, "fset", None)

                def safe_read_only_getter(self, _getter=original_getter):
                    return _normalize_safe_cell_value(_getter(self))

                setattr(ReadOnlyCell, "value", property(safe_read_only_getter, original_setter))
                patched.append((ReadOnlyCell, original_property))

        yield
    finally:
        for cls, original_property in reversed(patched):
            setattr(cls, "value", original_property)


def _run_code_once(code, safe_openpyxl_values=False):
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = captured_out = StringIO()
    sys.stderr = captured_err = StringIO()
    success = True
    patch_context = _safe_openpyxl_value_patch() if safe_openpyxl_values else nullcontext()
    try:
        with patch_context:
            exec(code, {"__builtins__": __builtins__})
    except Exception as e:
        success = False
        captured_err.write(f"{type(e).__name__}: {e}\n")
        captured_err.write(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    output = captured_out.getvalue()
    error = captured_err.getvalue()
    return (output if success else f"Error:\n{error}"), success


def _should_retry_with_safe_openpyxl(output_text):
    """Retry only for mixed comparison failures this patch is meant to fix."""
    return (
        "TypeError" in output_text
        and bool(
            re.search(
                (
                    r"not supported between instances of "
                    r"'(str|int|float|datetime|datetime\.datetime|date)' and "
                    r"'(str|int|float|datetime|datetime\.datetime|date)'"
                ),
                output_text,
            )
        )
    )


def execute_code(code, timeout_hint=None):
    """
    Execute Python code locally, capturing stdout/stderr.

    Args:
        code: Python source code string
        timeout_hint: unused, reserved for future subprocess-based timeout

    Returns:
        (output_text, success_bool)
    """
    if not code or not code.strip():
        return "Error: empty code", False

    output, success = _run_code_once(code)
    if success or not _should_retry_with_safe_openpyxl(output):
        return output, success

    retry_output, retry_success = _run_code_once(code, safe_openpyxl_values=True)
    if retry_success:
        return retry_output, True
    return (
        "Retried with safe openpyxl cell values after a mixed-type comparison error.\n"
        f"{retry_output}",
        False,
    )


def summarize_execution_error(code, exec_output, context_radius=3):
    """
    Build a compact execution error payload for LLM-based repair.

    Returns a dict with:
      - error_summary: short text summary
      - line_number: failing line number in generated code, if available
      - code_context: nearby code lines around the failure
      - relevant_traceback: compact traceback excerpt
    """
    text = exec_output or ""
    if text.startswith("Error:\n"):
        text = text[len("Error:\n"):]

    lines = [line for line in text.splitlines() if line.strip()]
    error_line = lines[0] if lines else "Unknown execution error"

    line_number = None
    for raw_line in lines:
        match = re.search(r'File "<string>", line (\d+)', raw_line)
        if match:
            line_number = int(match.group(1))
            break

    code_context = ""
    if line_number is not None and code:
        code_lines = code.splitlines()
        start = max(1, line_number - context_radius)
        end = min(len(code_lines), line_number + context_radius)
        code_context = "\n".join(
            f"{idx}: {code_lines[idx - 1]}"
            for idx in range(start, end + 1)
        )

    traceback_lines = []
    for idx, raw_line in enumerate(lines):
        if raw_line.startswith("Traceback "):
            traceback_lines = lines[idx:]
            break
    if traceback_lines:
        relevant_traceback = "\n".join(traceback_lines[:12])
    else:
        relevant_traceback = "\n".join(lines[:12])

    summary_parts = [error_line]
    if line_number is not None:
        summary_parts.append(f"Likely failing generated code line: {line_number}.")
    if code_context:
        summary_parts.append("Code context is attached below.")
    error_summary = " ".join(summary_parts)

    return {
        "error_summary": error_summary,
        "line_number": line_number,
        "code_context": code_context,
        "relevant_traceback": relevant_traceback,
    }


def adapt_code_for_test_case(code, original_id, original_idx, new_idx):
    """
    Adapt generated code for a different test case by replacing file paths.

    Replaces patterns like:
      1_59196_input.xlsx  -> 2_59196_input.xlsx
      1_59196_output.xlsx -> 2_59196_output.xlsx

    Args:
        code: original Python code string
        original_id: sample ID (e.g. "59196")
        original_idx: original test case index (e.g. 1)
        new_idx: new test case index (e.g. 2)

    Returns:
        adapted code string
    """
    adapted = code.replace(
        f"{original_idx}_{original_id}_input.xlsx",
        f"{new_idx}_{original_id}_input.xlsx",
    )
    adapted = adapted.replace(
        f"{original_idx}_{original_id}_output.xlsx",
        f"{new_idx}_{original_id}_output.xlsx",
    )
    return adapted
