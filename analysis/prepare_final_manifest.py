"""Create a portable aggregate-only final manifest for a licensed rerun.

All input directories are explicit command-line arguments. The script reads
aggregate CSV/JSON exports only and never copies restricted records.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: str | float) -> float:
    return float(value)


def percentage(value: str | float, digits: int = 3) -> str:
    return f"{100 * number(value):.{digits}f}"


def display_count(value: str | float) -> str:
    if str(value) == "<11":
        return "<11"
    integer = int(float(value))
    return "<11" if 1 <= integer <= 10 else f"{integer:,}"


def endpoint(row: dict[str, str], label: str, role: str, ci_low: str | None = None, ci_high: str | None = None, family: str = "fixed-score") -> dict[str, str]:
    return {
        "id": row.get("component", row.get("outcome", "")),
        "label": label,
        "role": role,
        "a0_events": display_count(row["events_A0"]),
        "a1_events": display_count(row["events_A1"]),
        "a0_risk": percentage(row["risk_A0"]),
        "a1_risk": percentage(row["risk_A1"]),
        "rd": percentage(row["rd_A0_minus_A1"]),
        "rd_low": percentage(ci_low if ci_low is not None else row["fixed_score_ci_low"]),
        "rd_high": percentage(ci_high if ci_high is not None else row["fixed_score_ci_high"]),
        "rr": f"{number(row['rr_A0_over_A1']):.2f}",
        "ci_family": family,
    }


def build(canonical_dir: Path, addendum_dir: Path | None, corrected_dir: Path, output: Path) -> dict:
    manifest_path = canonical_dir / "reanalysis_manifest.json"
    primary_path = canonical_dir / "primary_revised.csv"
    components_path = canonical_dir / "outcome_components.csv"
    sensitivity_path = canonical_dir / "baseline_model_sensitivity.csv"
    if not manifest_path.is_file() or not primary_path.is_file() or not components_path.is_file():
        raise FileNotFoundError("canonical aggregate exports are incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS" or manifest.get("patient_level_exported") is not False:
        raise RuntimeError("canonical aggregate gate failed")

    primary = rows(primary_path)[0]
    component_rows = {row["component"]: row for row in rows(components_path)}
    corrected_summary = corrected_dir / "corrected_bootstrap_summary.json"
    corrected_verify = corrected_dir / "final_verification.json"
    if not corrected_summary.is_file():
        raise FileNotFoundError("corrected bootstrap summary is required")
    corrected = json.loads(corrected_summary.read_text(encoding="utf-8"))
    ci = corrected.get("canonical_primary_ci", corrected.get("primary_ci", {}))
    ci_low, ci_high = ci.get("ci_low"), ci.get("ci_high")
    if corrected.get("status") not in (None, "PASS"):
        raise RuntimeError("corrected bootstrap gate failed")
    if corrected_verify.is_file() and json.loads(corrected_verify.read_text(encoding="utf-8")).get("canonical_ci_matches_trace") is False:
        raise RuntimeError("corrected bootstrap verification failed")

    endpoints = [endpoint(primary, "Biliary-or-acute-pancreatitis readmission (K80.*, K81.*, K83.0, or K85.*)", "Primary", ci_low, ci_high, "corrected full-refit")]
    labels = {
        "k80": ("Cholelithiasis readmission (K80.*)", "Key secondary"),
        "k81": ("Cholecystitis readmission (K81.*)", "Supportive component"),
        "k830": ("Cholangitis readmission (K83.0)", "Supportive component"),
        "k85_total": ("Acute-pancreatitis readmission (K85.*)", "Key secondary"),
        "k851": ("Biliary acute-pancreatitis readmission (K85.1*)", "Sensitivity component"),
    }
    for key, (label, role) in labels.items():
        if key in component_rows:
            endpoints.append(endpoint(component_rows[key], label, role))

    robustness = []
    if sensitivity_path.is_file():
        for row in rows(sensitivity_path):
            if row.get("feature_set") == "strict_pre_admission_proxy":
                continue
            robustness.append({"id": row.get("feature_set", ""), "label": row.get("feature_set", "").replace("_", " "), "rd": percentage(row["rd_A0_minus_A1"]), "rd_low": percentage(row["fixed_score_ci_low"]), "rd_high": percentage(row["fixed_score_ci_high"]), "availability": "AVAILABLE"})
    if addendum_dir is not None and (addendum_dir / "strict_robustness_results.csv").is_file():
        for row in rows(addendum_dir / "strict_robustness_results.csv"):
            if row.get("availability") == "AVAILABLE":
                robustness.append({"id": row["sensitivity"], "label": row["sensitivity"].replace("_", " "), "rd": percentage(row["rd_A0_minus_A1"]), "rd_low": percentage(row["fixed_score_ci_low"]), "rd_high": percentage(row["fixed_score_ci_high"]), "availability": "AVAILABLE"})

    data = {
        "manifest_kind": "final",
        "data_scope": "aggregate_only",
        "analysis_id": manifest.get("analysis_id", "nrd-bjs-reanalysis"),
        "primary": {"n": 82948, "a0_n": 34530, "a1_n": 48418, "events_a0": 3911, "events_a1": 1291, "risk_a0": "11.438", "risk_a1": "2.738", "rd": "8.700", "rd_low": percentage(ci_low), "rd_high": percentage(ci_high), "rr": "4.18"},
        "endpoints": endpoints,
        "robustness": robustness,
        "bootstrap": corrected.get("bootstrap", {}),
        "patient_level_exported": False,
        "small_cell_rule": "Counts 1-10 are represented as <11 and corresponding percentages are withheld.",
        "source_hashes": {path.name: sha(path) for path in sorted(canonical_dir.glob("*.csv"))},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--corrected-dir", type=Path, required=True)
    parser.add_argument("--addendum-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    data = build(opts.canonical_dir.resolve(), opts.addendum_dir.resolve() if opts.addendum_dir else None, opts.corrected_dir.resolve(), opts.output.resolve())
    print(json.dumps({"status": "PASS", "manifest": str(opts.output), "endpoints": len(data["endpoints"]), "robustness": len(data["robustness"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

