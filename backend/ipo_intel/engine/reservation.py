"""Reserved shares per category, with manual overrides from the RHP.

IndianAPI's reserved figures can be wrong (anchor portions, employee or
shareholder quotas, late changes), so every IPO shows a 'verify against the
RHP' warning until each category is overridden by hand. An override also
recomputes times subscribed = applied / reserved.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

VERIFY_RHP = "Reserved shares come from IndianAPI; verify them against the RHP."


@dataclass(frozen=True)
class CategoryFigures:
    category: str
    applied_shares: int
    reserved_shares: int
    times: float
    source: str  # 'indianapi' or 'manual'


def apply_overrides(
    figures: Mapping[str, tuple[int, int, float]],
    overrides: Mapping[str, int],
) -> tuple[dict[str, CategoryFigures], list[str]]:
    """`figures`: category -> (applied, reserved, times) as IndianAPI reports them.

    Returns the figures to use and any warnings. 'overall' is recomputed from
    the parts when any part is overridden.
    """
    result: dict[str, CategoryFigures] = {}
    for category, (applied, reserved, times) in figures.items():
        manual = overrides.get(category)
        if manual is not None and manual > 0:
            result[category] = CategoryFigures(category, applied, manual, applied / manual, "manual")
        else:
            result[category] = CategoryFigures(category, applied, reserved, times, "indianapi")

    parts = [result[c] for c in ("retail", "nii", "qib") if c in result]
    if "overall" in result and any(part.source == "manual" for part in parts):
        applied = sum(part.applied_shares for part in parts)
        reserved = sum(part.reserved_shares for part in parts)
        if reserved > 0:
            result["overall"] = CategoryFigures("overall", applied, reserved, applied / reserved, "manual")

    warnings = [VERIFY_RHP] if any(item.source == "indianapi" for item in parts) or not parts else []
    return result, warnings
