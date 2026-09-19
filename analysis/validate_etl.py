#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from nrd_analysis import derive_comorbidity, load_cohort


EXPECTED_STAGE1 = {"n": 82192, "A0": 34090, "A1": 48102}


def missing_count(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].isna().sum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()

    cohorts = {name: load_cohort(root, name) for name in ("main", "broad", "harm")}
    main = cohorts["main"]
    dx_cols = [f"DX{i}" for i in range(1, 41)]

    assertions = {
        "main_index_id_unique": bool(main["index_id"].is_unique),
        "main_person_id_unique_within_year": bool(~main.duplicated(["year", "person_id"]).any()),
        "main_dx1_exact_K8510": bool(main["DX1"].fillna("").eq("K8510").all()),
        "main_survived_discharge": bool(main["DIED"].eq(0).all()),
        "main_discharge_month_1_to_9": bool(main["DMONTH"].between(1, 9).all()),
        "main_binary_exposure": bool(main["A"].isin([0, 1]).all()),
        "main_positive_design_weight": bool(main["DISCWT"].gt(0).all()),
        "main_nonmissing_stratum": bool(main["stratum_id"].notna().all()),
        "main_nonmissing_hospital_cluster": bool(main["hospital_cluster"].notna().all()),
        "outcome_window_monotonic": bool(
            (main["y30_primary_middle"] <= main["y60_primary_middle"]).all()
            and (main["y60_primary_middle"] <= main["y90_primary_middle"]).all()
        ),
        "narrow_nested_in_middle": bool((main["y90_narrow_principal"] <= main["y90_primary_middle"]).all()),
        "middle_nested_in_wide": bool((main["y90_primary_middle"] <= main["y90_wide_any_dx"]).all()),
    }

    sample_n = min(2000, len(main))
    sample = main.sample(sample_n, random_state=20260911).reset_index(drop=True)
    derived = derive_comorbidity(sample)
    eli_cols = [column for column in derived if column.startswith("eli_")]
    cci_cols = [column for column in derived if column.startswith("cci_")]
    assertions.update({
        "comorbidipy_elixhauser_columns_present": bool(eli_cols),
        "comorbidipy_charlson_columns_present": bool(cci_cols),
        "hfrs_column_present": "hfrs" in derived,
        "hfrs_only_applicable_age_75_plus": bool(derived.loc[derived["AGE"].lt(75), "hfrs"].isna().all()),
    })

    event_by_year_arm = (
        main.groupby(["year", "A"], observed=True)
        .agg(n=("index_id", "size"), events=("y90_primary_middle", "sum"),
             weighted_events=("y90_primary_middle", lambda x: float(np.average(x, weights=main.loc[x.index, "DISCWT"]))))
        .reset_index()
        .to_dict(orient="records")
    )
    total = {"n": int(len(main)), "A0": int(main["A"].eq(0).sum()), "A1": int(main["A"].eq(1).sum())}
    report = {
        "status": "PASS" if all(assertions.values()) else "FAIL",
        "assertions": assertions,
        "rows": {name: int(len(frame)) for name, frame in cohorts.items()},
        "main_counts": total,
        "stage1_counts": EXPECTED_STAGE1,
        "stage1_to_v2_delta": {key: total[key] - EXPECTED_STAGE1[key] for key in total},
        "v2_change_explanation": "Acute/acute-on-chronic respiratory failure J96.0*/J96.2* replaces the Stage-1 all-J96 exclusion; chronic respiratory failure alone is no longer treated as acute organ failure.",
        "missing": {
            column: missing_count(main, column)
            for column in [
                "DISCWT", "hospital_cluster", "stratum_id", "APRDRG_Severity",
                "HOSP_BEDSIZE", "HOSP_URCAT4", "HOSP_UR_TEACH", "H_CONTRL",
                "CCR_NRD", "facility_cost_nominal",
            ]
        },
        "event_by_year_arm": event_by_year_arm,
        "same_day_records": int(main["same_day_records"].sum()),
        "overlapping_records": int(main["overlapping_records"].sum()),
        "A1_invalid_or_missing_chole_prday": int(
            main.loc[main["A"].eq(1), "chole_prday_all_missing_or_invalid"].eq(1).sum()
        ),
        "sample_comorbidity_columns": {"elixhauser": eli_cols, "charlson": cci_cols},
        "patient_level_data_exported": False,
    }
    target = root / "04_qc" / "etl_validation_report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
