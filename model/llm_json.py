"""Parse JSON objects from LLM replies (markdown fences or raw braces)."""

import json
import re
from typing import Any, Optional

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")


def extract_json_from_text(text: str) -> Optional[dict[str, Any]]:
    for pattern in (_JSON_FENCE, _JSON_OBJECT):
        match = pattern.search(text or "")
        if not match:
            continue
        raw = match.group(1) if match.lastindex else match.group(0)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, IndexError):
            continue
    return None
