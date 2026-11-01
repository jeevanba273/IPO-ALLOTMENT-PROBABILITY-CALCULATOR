"""Which investor category an application falls in, by its amount at the cut-off price."""

from __future__ import annotations

import math
from enum import StrEnum

from ipo_intel.engine.rules import Rules

MIN_BID_IS_NII = "your minimum bid is NII for this IPO"


class Category(StrEnum):
    RETAIL = "retail"
    SNII = "snii"
    BNII = "bnii"

    @property
    def book(self) -> str:
        """The subscription book it is counted in: 'retail' or 'nii'."""
        return "retail" if self is Category.RETAIL else "nii"

    @property
    def label(self) -> str:
        return {"retail": "Retail", "snii": "sNII (₹2–10 lakh)", "bnii": "bNII (above ₹10 lakh)"}[self.value]


def category_for(amount_rs: float, rules: Rules) -> Category:
    if amount_rs <= 0:
        raise ValueError("application amount must be positive")
    if amount_rs <= rules.categories.retail_max_rs:
        return Category.RETAIL
    if amount_rs <= rules.categories.snii_max_rs:
        return Category.SNII
    return Category.BNII


def application_amount(lots: int, lot_size: int, cutoff: float) -> float:
    return lots * lot_size * cutoff


def max_retail_lots(lot_size: int, cutoff: float, rules: Rules) -> int:
    """The most lots that still count as retail (0 if even one lot is above the retail limit)."""
    return math.floor(rules.categories.retail_max_rs / (lot_size * cutoff))


def nii_share(category: Category, rules: Rules) -> float:
    """Fraction of the NII reservation open to this NII sub-category."""
    split = rules.categories.nii_split
    return {Category.SNII: split.snii, Category.BNII: split.bnii}[category]
