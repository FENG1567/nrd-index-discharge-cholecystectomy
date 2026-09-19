from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from nrd_build_cohort import ercp_related, outcome_flags, severe_hit, starts_any  # noqa: E402


class CodebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.codebook = json.loads((ROOT / "codebook" / "codebook_v2.0.json").read_text(encoding="utf-8"))

    def test_main_and_broad_diagnosis_boundaries(self) -> None:
        self.assertIn("K8510", self.codebook["cohort"]["main_principal_dx_exact"])
        self.assertNotIn("K8511", self.codebook["cohort"]["main_principal_dx_exact"])
        self.assertTrue(starts_any("K8511", self.codebook["cohort"]["broad_principal_dx_prefix"]))

    def test_acute_respiratory_failure_excludes_chronic_only(self) -> None:
        empty_pr = tuple("" for _ in range(25))
        acute = tuple(["K8510", "J9601"] + [""] * 38)
        chronic = tuple(["K8510", "J9611"] + [""] * 38)
        self.assertTrue(severe_hit(acute, empty_pr, self.codebook)[0])
        self.assertFalse(severe_hit(chronic, empty_pr, self.codebook)[0])

    def test_life_support_and_exposure_boundaries(self) -> None:
        dx = tuple(["K8510"] + [""] * 39)
        ventilation = tuple(["5A1945Z"] + [""] * 24)
        dialysis = tuple(["5A1D70Z"] + [""] * 24)
        self.assertTrue(severe_hit(dx, ventilation, self.codebook)[0])
        self.assertTrue(severe_hit(dx, dialysis, self.codebook)[0])
        self.assertTrue(starts_any("0FT44ZZ", self.codebook["exposure"]["cholecystectomy_any_pr_prefix"]))
        self.assertFalse(starts_any("0FB44ZZ", self.codebook["exposure"]["cholecystectomy_any_pr_prefix"]))

    def test_outcome_hierarchy(self) -> None:
        blank_pr = tuple("" for _ in range(25))
        cholangitis = tuple(["K8301"] + [""] * 39)
        incidental_biliary = tuple(["I10", "K829"] + [""] * 38)
        self.assertTrue(outcome_flags(cholangitis, blank_pr, self.codebook)["primary_middle"])
        self.assertFalse(outcome_flags(incidental_biliary, blank_pr, self.codebook)["primary_middle"])
        self.assertTrue(outcome_flags(incidental_biliary, blank_pr, self.codebook)["wide_any_dx"])

    def test_ercp_anatomic_algorithm(self) -> None:
        self.assertTrue(ercp_related("0FC98ZZ", self.codebook))
        self.assertFalse(ercp_related("0FT44ZZ", self.codebook))
        self.assertFalse(ercp_related("0FC94ZZ", self.codebook))

    def test_post_treatment_blacklist(self) -> None:
        baseline = set(self.codebook["model_baseline_domains"])
        blacklist = set(self.codebook["principles"]["baseline_blacklist"])
        self.assertTrue(baseline.isdisjoint(blacklist))


if __name__ == "__main__":
    unittest.main(verbosity=2)

