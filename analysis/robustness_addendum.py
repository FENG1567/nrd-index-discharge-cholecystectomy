#!/usr/bin/env python3
"""Aggregate-only strict-model robustness addendum for the BJS revision.

This program deliberately leaves the canonical analysis directory untouched.
All newly fitted models use the identical strict pre-admission-proxy covariate
set as the canonical model.  Their uncertainty is a labelled fixed-score,
stratified hospital-cluster bootstrap (not the canonical full-refit bootstrap).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL = (
    "bjs_revision_results/primary_revised.csv",
    "bjs_revision_results/reanalysis_manifest.json",
    "bjs_revision_results/outcome_components.csv",
    "bjs_revision_results/full_refit_bootstrap_strict_server_only.csv",
)
SMALL_CELL = 11
PRIMARY = "y90_primary_middle"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        os.close(fd)
        frame.to_csv(temp, index=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_primary_module(root: Path):
    source = root / "05_models" / "ate" / "bjs_reanalysis.py"
    spec = importlib.util.spec_from_file_location("bjs_primary_module", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load canonical strict-model implementation")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, source


def censor(value: int | float) -> int | float | str:
    if 0 < int(value) < SMALL_CELL:
        return "<11"
    return int(value) if float(value).is_integer() else float(value)


def canonical_hashes(root: Path) -> dict[str, str]:
    return {name: digest(root / name) for name in CANONICAL}


def cohort_audit(name: str, frame: pd.DataFrame, definition: str, feasible: bool = True, reason: str = "") -> dict:
    if not feasible:
        return {"sensitivity": name, "feasible": False, "reason": reason, "cohort_definition": definition}
    required = {"A", PRIMARY, "stratum_id", "hospital_cluster", "DISCWT"}
    missing = sorted(required - set(frame.columns))
    if missing:
        return {"sensitivity": name, "feasible": False, "reason": "required columns unavailable: " + ", ".join(missing), "cohort_definition": definition}
    a = frame["A"].astype(int)
    return {
        "sensitivity": name,
        "feasible": bool(len(frame) > 0 and set(a.unique()).issubset({0, 1}) and a.nunique() == 2),
        "reason": "" if len(frame) > 0 and a.nunique() == 2 else "empty cohort or one exposure group after filter",
        "cohort_definition": definition,
        "n": int(len(frame)),
        "A0_n": censor(int((a == 0).sum())),
        "A1_n": censor(int((a == 1).sum())),
        "events_A0": censor(int(frame.loc[a == 0, PRIMARY].sum())),
        "events_A1": censor(int(frame.loc[a == 1, PRIMARY].sum())),
        "n_strata": int(frame["stratum_id"].nunique(dropna=True)),
        "n_hospital_clusters": int(frame["hospital_cluster"].nunique(dropna=True)),
    }


def privacy_audit(out: Path) -> dict:
    prohibited = {"key_nrd", "nrd_visitlink", "person_id", "index_id", "hospital_cluster"}
    files = []
    for path in sorted(out.glob("*.csv")):
        data = pd.read_csv(path)
        bad = [column for column in data.columns if column.lower() in prohibited]
        count_columns = [
            column for column in data.columns
            if column.lower() in {"n", "a0_n", "a1_n", "missing_n", "events_a0", "events_a1"}
            or column.lower().endswith("_n") or "events" in column.lower()
        ]
        small = []
        for column in count_columns:
            for value in data[column].dropna().tolist():
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if 1 <= numeric <= 10:
                    small.append({"column": column, "value": value})
        files.append({"file": path.name, "identifier_columns": bad, "unsuppressed_small_counts": small, "pass": not bad and not small})
    return {"status": "PASS" if all(item["pass"] for item in files) else "FAIL", "patient_level_exported": False, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fixed-bootstrap", type=int, default=1000)
    args = parser.parse_args()
    root = args.root.resolve()
    out = root / "bjs_revision_robustness"
    out.mkdir(exist_ok=True)
    primary, primary_source = load_primary_module(root)
    before = canonical_hashes(root)
    main = primary.add_quarter(pd.read_parquet(root / "03_etl" / "analysis_ready_main.parquet"))

    candidates: list[tuple[str, pd.DataFrame, str, str]] = [
        ("resident_only", main.loc[main["RESIDENT"].eq(1)].copy(), "RESIDENT=1 in the frozen main cohort", "strict pre-admission proxy model; fixed-score stratified hospital-cluster bootstrap"),
        ("aprdrg_severity_1_2", main.loc[main["APRDRG_Severity"].isin([1, 2])].copy(), "APRDRG_Severity in {1,2} in the frozen main cohort; post-admission severity used only as an eligibility sensitivity, never as an adjustment covariate", "strict pre-admission proxy model; fixed-score stratified hospital-cluster bootstrap"),
        ("exclude_all_J96", main.loc[main["any_J96"].eq(0)].copy(), "Exclude any index admission with any_J96=1; this is a stricter respiratory/life-support proxy eligibility sensitivity, not a model covariate", "strict pre-admission proxy model; fixed-score stratified hospital-cluster bootstrap"),
    ]
    broad_paths = sorted((root / "03_etl").glob("year=*/broad_cohort.parquet"))
    broad = pd.concat([pd.read_parquet(path) for path in broad_paths], ignore_index=True) if broad_paths else pd.DataFrame()
    broad = primary.add_quarter(broad) if len(broad) else broad
    broad_definition = "Existing broad cohort: age >=18, principal diagnosis starts with K851 (K85.1*), canonical severe-code exclusions, survived discharge, discharge months 1-9, earliest annual index admission"
    candidates.append(("broad_K851_phenotype", broad, broad_definition, "strict pre-admission proxy model; fixed-score stratified hospital-cluster bootstrap"))

    audits = []
    rows = []
    strict_covariates = primary.CAT + primary.PRIOR_NUM
    for name, frame, definition, provenance in candidates:
        audit = cohort_audit(name, frame, definition)
        audits.append(audit)
        if not audit["feasible"]:
            rows.append({"sensitivity": name, "availability": "NOT_AVAILABLE", "reason": audit["reason"], "cohort_definition": definition, "ci_provenance": "No estimate generated"})
            continue
        if name == "broad_K851_phenotype" and not frame["DX1"].fillna("").astype(str).str.startswith("K851").all():
            rows.append({"sensitivity": name, "availability": "NOT_AVAILABLE", "reason": "existing broad cohort does not satisfy documented K851 principal-diagnosis definition", "cohort_definition": definition, "ci_provenance": "No estimate generated"})
            continue
        result, _ = primary.model_result(frame, PRIMARY, "strict_pre_admission_proxy", args.fixed_bootstrap)
        result.update({
            "sensitivity": name,
            "availability": "AVAILABLE",
            "reason": "",
            "cohort_definition": definition,
            "ci_provenance": provenance,
            "full_nuisance_refit_bootstrap": "Not performed; reserved for canonical primary result only",
            "strict_covariates": json.dumps(strict_covariates),
            "hfrs_in_model": False,
            "post_treatment_covariates_in_model": False,
        })
        rows.append(result)

    existing = pd.read_csv(root / "bjs_revision_results" / "baseline_model_sensitivity.csv")
    for feature, label, definition in [
        ("chronic_proxy_sensitivity", "chronic_proxy_main_cohort", "Main cohort; chronic diagnosis proxies added only as an explicit sensitivity because present-on-admission is unavailable"),
        ("original_extended_sensitivity", "legacy_expanded_main_cohort", "Main cohort; legacy extended model including acute discharge diagnoses, reported only for transparent comparison"),
    ]:
        old = existing.loc[existing["feature_set"].eq(feature)].iloc[0].to_dict()
        old.update({
            "sensitivity": label,
            "availability": "AVAILABLE",
            "reason": "Previously computed in the frozen revision analysis; copied without refitting",
            "cohort_definition": definition,
            "ci_provenance": "Existing fixed-score stratified hospital-cluster bootstrap and EIF comparison; neither is the canonical full-refit primary CI",
            "full_nuisance_refit_bootstrap": "Not performed; reserved for canonical primary result only",
            "strict_covariates": json.dumps(strict_covariates),
            "hfrs_in_model": False,
            "post_treatment_covariates_in_model": False,
        })
        rows.append(old)

    results = pd.DataFrame(rows)
    write_csv(pd.DataFrame(audits), out / "cohort_feasibility_audit.csv")
    write_csv(results, out / "strict_robustness_results.csv")
    after = canonical_hashes(root)
    if before != after:
        raise RuntimeError("canonical primary files changed during robustness analysis")
    summary = {
        "status": "PASS",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "canonical_files_unchanged": True,
        "canonical_hashes_before_after": {name: {"before": before[name], "after": after[name]} for name in CANONICAL},
        "strict_covariates": strict_covariates,
        "strict_excludes": ["HFRS", "APRDRG_Severity as covariate", "APRDRG_Risk_Mortality", "LOS", "TOTCHG", "DISPUNIFORM", "REHABTRANSFER", "index_ercp_related", "chole_prday_min", "same-index acute biliary diagnoses"],
        "uncertainty": "Sensitivity results use fixed-score stratified hospital-cluster bootstrap and EIF comparison. The sole full nuisance-refit bootstrap CI remains canonical primary only.",
        "patient_level_exported": False,
        "files": {path.name: digest(path) for path in sorted(out.glob("*.csv"))},
        "code_sha256": digest(primary_source),
    }
    audit = privacy_audit(out)
    if audit["status"] != "PASS":
        raise RuntimeError("privacy audit failed")
    atomic_text(out / "privacy_audit.json", json.dumps(audit, indent=2, sort_keys=True) + "\n")
    atomic_text(out / "robustness_manifest.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    # Do not checksum the live stdout/stderr/PID files: stdout receives the
    # terminal PASS line after this program writes its deliverables.  The
    # integrity list covers the immutable code and aggregate result artefacts.
    immutable = [
        out / "robustness_addendum.py",
        out / "cohort_feasibility_audit.csv",
        out / "strict_robustness_results.csv",
        out / "privacy_audit.json",
        out / "robustness_manifest.json",
    ]
    hashes = [f"{digest(path)}  {path.name}" for path in immutable]
    atomic_text(out / "SHA256SUMS.txt", "\n".join(hashes) + "\n")
    print(json.dumps({"status": "PASS", "available": int((results.get("availability") == "AVAILABLE").sum()), "not_available": int((results.get("availability") == "NOT_AVAILABLE").sum()), "canonical_files_unchanged": before == after, "patient_level_exported": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
