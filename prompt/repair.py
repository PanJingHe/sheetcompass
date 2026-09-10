"""Prompt templates for repairing previously generated spreadsheet code."""

CODE_REPAIR_SYSTEM = "Fix the script; same task intent; one full ```python``` block only."

CODE_REPAIR_PROMPT = """Execution failed. Fix the script; keep the same task intent.

## Instruction
{instruction}

## Code
```python
{previous_code}
```

## Error
{error_summary}

## Near failure
```python
{code_context}
```

Return one complete Python script (code block only)."""
