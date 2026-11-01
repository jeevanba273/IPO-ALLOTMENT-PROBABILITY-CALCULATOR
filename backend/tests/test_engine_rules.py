from pathlib import Path

import pytest
import yaml

from ipo_intel.config import REPO_ROOT
from ipo_intel.engine.rules import DEFAULT_RULES_PATH, RulesError, load_rules


def test_default_rules_match_the_spec():
    rules = load_rules()
    assert DEFAULT_RULES_PATH == REPO_ROOT / "config" / "rules_v1.yaml"
    assert rules.categories.retail_max_rs == 200_000
    assert rules.categories.snii_max_rs == 1_000_000
    assert rules.categories.nii_split.snii == pytest.approx(1 / 3, abs=0.001)
    assert rules.retail.avg_min_bids_per_applicant.for_board("mainboard") == 1.5
    assert rules.retail.avg_min_bids_per_applicant.for_board("sme") == 1.0
    assert rules.nii.method == "proportionate"
    assert rules.ev.gmp_multipliers == (0.5, 0.75, 1.0)
    assert rules.projection.min_samples >= 1


def _write(tmp_path: Path, change) -> Path:
    raw = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r["categories"]["nii_split"].update(snii=0.5), "must be 1"),
        (lambda r: r["categories"].update(snii_max_rs=100_000), "snii_max_rs must be above retail_max_rs"),
        (lambda r: r["retail"]["avg_min_bids_per_applicant"].update(sme=0.5), "avg_min_bids_per_applicant.sme"),
        (lambda r: r["ev"].update(gmp_multipliers=[1.0, 0.5]), "ascending"),
        (lambda r: r["ev"].update(gmp_multipliers=[0, 1]), "above 0"),
        (lambda r: r["projection"].update(min_samples=0), "projection.min_samples"),
        (lambda r: r.update(version=2), "version"),
        (lambda r: r.update(surprise=True), "surprise"),
    ],
)
def test_bad_values_are_named(tmp_path, change, message):
    with pytest.raises(RulesError) as caught:
        load_rules(_write(tmp_path, change))
    assert message in str(caught.value)


def test_missing_or_broken_file(tmp_path):
    with pytest.raises(RulesError, match="not found"):
        load_rules(tmp_path / "nope.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("categories: [unclosed", encoding="utf-8")
    with pytest.raises(RulesError, match="not valid YAML"):
        load_rules(broken)
