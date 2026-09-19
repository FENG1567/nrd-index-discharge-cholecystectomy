from __future__ import annotations

import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


class PublicNumericTests(unittest.TestCase):
    def test_cohort_counts_and_primary_estimate(self) -> None:
        summary = json.loads((ROOT / "public_results" / "analysis_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["counts"], {"n": 82948, "A0": 34530, "A1": 48418, "primary_events_A0": 3911, "primary_events_A1": 1291})
        self.assertAlmostEqual(summary["primary"]["rd_percent"], 8.700, places=3)
        self.assertEqual((summary["primary"]["ci_low_percent"], summary["primary"]["ci_high_percent"]), (8.287, 9.080))

    def test_annual_overall_row_matches_summary(self) -> None:
        summary = json.loads((ROOT / "public_results" / "analysis_summary.json").read_text(encoding="utf-8"))
        annual = pd.read_csv(ROOT / "public_results" / "record_reporting.csv")
        overall = annual.loc[annual["year"].astype(str).eq("overall")].iloc[0]
        self.assertEqual(int(overall.analysis_n), summary["counts"]["n"])
        self.assertEqual(int(overall.A0_n), summary["counts"]["A0"])
        self.assertEqual(int(overall.A1_n), summary["counts"]["A1"])

    def test_canonical_summary_is_aggregate_only(self) -> None:
        summary = json.loads((ROOT / "public_results" / "corrected_bootstrap_canonical_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["primary_rd"], "8.700")
        self.assertEqual(summary["primary_ci"], ["8.287", "9.080"])
        self.assertFalse(summary["patient_level_exported"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

