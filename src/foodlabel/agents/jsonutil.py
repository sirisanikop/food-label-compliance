"""Tolerant JSON extraction from model output."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Return the first JSON object found in text, or None."""
    if not text:
        return None
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    for cand in candidates:
        try:
            val = json.loads(cand)
            if isinstance(val, dict):
                return val
        except ValueError:
            pass
        start = cand.find("{")
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            val = json.loads(cand[start : i + 1])
                            if isinstance(val, dict):
                                return val
                        except ValueError:
                            pass
                        break
            start = cand.find("{", start + 1)
    return None
