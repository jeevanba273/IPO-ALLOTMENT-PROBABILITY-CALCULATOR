"""Loading and validating config/rules_v1.yaml."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ipo_intel.config import REPO_ROOT

DEFAULT_RULES_PATH = REPO_ROOT / "config" / "rules_v1.yaml"


class RulesError(ValueError):
    """The rules file is missing, unreadable or has a bad value."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NiiSplit(_Model):
    snii: float = Field(gt=0, lt=1)
    bnii: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def _adds_up(self) -> NiiSplit:
        if not math.isclose(self.snii + self.bnii, 1.0, abs_tol=0.001):
            raise ValueError(f"snii + bnii must be 1, got {self.snii + self.bnii:g}")
        return self


class Categories(_Model):
    retail_max_rs: int = Field(gt=0)
    snii_max_rs: int = Field(gt=0)
    nii_split: NiiSplit

    @model_validator(mode="after")
    def _ordered(self) -> Categories:
        if self.snii_max_rs <= self.retail_max_rs:
            raise ValueError("snii_max_rs must be above retail_max_rs")
        return self


class PerBoard(_Model):
    mainboard: float = Field(ge=1)
    sme: float = Field(ge=1)

    def for_board(self, board: str) -> float:
        return self.sme if board == "sme" else self.mainboard


class Retail(_Model):
    avg_min_bids_per_applicant: PerBoard


class NiiPerSub(_Model):
    snii: float = Field(ge=1)
    bnii: float = Field(ge=1)


class Nii(_Model):
    # 'proportionate': expected shares = applied / times.
    # 'sebi_2022': SEBI's rule since April 2022: the minimum NII bid by lottery first,
    # then any shares left over proportionately.
    method: Literal["proportionate", "sebi_2022"]
    # sebi_2022 only: average NII application, in minimum NII bids (sets the high end of the odds).
    avg_min_bids_per_applicant: NiiPerSub = NiiPerSub(snii=1.5, bnii=3.0)


class Ev(_Model):
    gmp_multipliers: tuple[float, ...] = Field(min_length=1)

    @field_validator("gmp_multipliers")
    @classmethod
    def _sane(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0 < value <= 2 for value in values):
            raise ValueError("each multiplier must be above 0 and at most 2")
        if list(values) != sorted(set(values)):
            raise ValueError("multipliers must be ascending without repeats")
        return values


class Projection(_Model):
    min_samples: int = Field(ge=1)
    min_base_times: float = Field(gt=0)


class Rules(_Model):
    version: Literal[1]
    categories: Categories
    retail: Retail
    nii: Nii
    ev: Ev
    projection: Projection


def load_rules(path: Path = DEFAULT_RULES_PATH) -> Rules:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RulesError(f"rules file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise RulesError(f"{path} is not valid YAML: {exc}") from None
    try:
        return Rules.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}" for error in exc.errors()
        )
        raise RulesError(f"{path}: {problems}") from None


@lru_cache(maxsize=4)
def cached_rules(path: Path = DEFAULT_RULES_PATH) -> Rules:
    return load_rules(path)
