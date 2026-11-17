"""Reference values that the regulations refer to but do not themselves state.

The FSSAI claims regulation sets many nutrient-content thresholds as a percentage of the Recommended
Dietary Allowance (RDA) - for example "Source of protein: 10 per cent. of RDA per 100 g for solids" -
without giving the RDA. The agent therefore needs a stated, sourced reference table. Keep it small and
cite it in every finding that uses it; if a nutrient is missing here the agent must answer needs_review.

Add rows only from a source you can cite. Values here are for a reference adult.
"""
from __future__ import annotations

from typing import Optional

RDA: dict[str, dict] = {
    "protein": {
        "value": 54.0,
        "unit": "g/day",
        "source": "ICMR-NIN Recommended Dietary Allowances 2020, reference adult man",
    },
}

_ALIASES = {"proteins": "protein"}


def get_reference(nutrient: str) -> Optional[dict]:
    key = (nutrient or "").strip().lower()
    key = _ALIASES.get(key, key)
    ref = RDA.get(key)
    return {"nutrient": key, **ref} if ref else None
