#!/usr/bin/env python3
"""Corrected grouped cluster bootstrap for the frozen BJS-revision cohort.

This is intentionally a standalone audit layer.  It imports (read-only) the
frozen nuisance-model functions, but resamples hospital clusters with copies
kept together in cross-fitting by an immutable original hospital-within-stratum
group.  Only aggregate summaries leave the server.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
import platform
import sys
import time
from pathlib import Path

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

SEED = 20260913
SMALL_CELL = 11


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{stamp()}] {message}", flush=True)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def censored(value: int | float) -> int | float | str:
    number = int(value) if float(value).is_integer() else float(value)
    return "<11" if 0 < float(value) < SMALL_CELL else number


def display_percent(count: int, denominator: int) -> float | None:
    return None if 0 < count < SMALL_CELL else (100.0 * count / denominator if denominator else None)


def load_base(source: Path):
    spec = importlib.util.spec_from_file_location("frozen_bjs_reanalysis", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to import frozen source: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_quarter(frame: pd.DataFrame) -> pd.DataFrame:
    copied = frame.copy()
    copied["quarter"] = ((pd.to_numeric(copied["DMONTH"], errors="coerce").fillna(1).astype(int) - 1) // 3 + 1).astype(int)
    return copied


def make_bootstrap_frame(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Resample hospitals within strata, retaining a copy-free grouping key.

    ``bootstrap_group`` records copy bookkeeping only.  ``original_cluster_group``
    is deliberately copy-free and is the *only* grouping key passed to GroupKFold.
    """
    pieces: list[pd.DataFrame] = []
    for stratum, subset in frame.groupby("stratum_id", sort=False, dropna=False):
        # A literal placeholder makes missing hospital codes matchable while
        # retaining their one-cluster-within-stratum bootstrap role.
        hospital_text = subset["hospital_cluster"].astype("string").fillna("__MISSING_HOSPITAL_CLUSTER__")
        hospitals = hospital_text.unique()
        if len(hospitals) == 0:
            raise RuntimeError("empty stratum encountered during bootstrap")
        chosen = rng.integers(0, len(hospitals), size=len(hospitals))
        stratum_text = str(stratum)
        for copy_number, position in enumerate(chosen):
            hospital = str(hospitals[position])
            part = subset.loc[hospital_text.eq(hospital)].copy()
            part["original_cluster_group"] = f"{stratum_text}|{hospital}"
            part["bootstrap_group"] = f"{stratum_text}|{hospital}|copy{copy_number}"
            pieces.append(part)
    assembled = pd.concat(pieces, ignore_index=True)
    if assembled.empty:
        raise RuntimeError("bootstrap draw was empty")
    return assembled


def assert_group_integrity(sample: pd.DataFrame, groups: np.ndarray, folds: int = 5) -> dict:
    """Prove that an original hospital group cannot appear in multiple folds."""
    if "original_cluster_group" not in sample:
        raise AssertionError("copy-free original_cluster_group missing")
    arms = sample["A"].to_numpy(int)
    unique_groups = np.unique(groups)
    split_count = min(folds, len(unique_groups))
    if split_count < 2:
        raise AssertionError("fewer than two copy-free original hospital groups")
    assigned = np.full(len(sample), -1, dtype=int)
    splitter = GroupKFold(n_splits=split_count)
    for fold, (train, test) in enumerate(splitter.split(sample, arms, groups)):
        train_groups = set(groups[train])
        test_groups = set(groups[test])
        if train_groups.intersection(test_groups):
            raise AssertionError("original hospital group crossed train/validation boundary")
        assigned[test] = fold
    if (assigned < 0).any():
        raise AssertionError("one or more rows were not assigned a cross-fit fold")
    fold_counts = pd.DataFrame({"group": groups, "fold": assigned}).groupby("group", sort=False)["fold"].nunique()
    leakage_count = int((fold_counts > 1).sum())
    if leakage_count:
        raise AssertionError(f"{leakage_count} original hospital groups appeared in multiple folds")
    return {"n_rows": int(len(sample)), "n_original_cluster_groups": int(len(unique_groups)), "folds": int(split_count), "groups_in_multiple_folds": leakage_count}


_FRAME: pd.DataFrame | None = None
_OUTCOME: str | None = None
_CAT: list[str] | None = None
_NUM: list[str] | None = None
_SEED: int | None = None
_BASE = None


def worker_init(frame: pd.DataFrame, outcome: str, cat: list[str], num: list[str], seed: int, source: str) -> None:
    global _FRAME, _OUTCOME, _CAT, _NUM, _SEED, _BASE
    _FRAME, _OUTCOME, _CAT, _NUM, _SEED = frame, outcome, cat, num, seed
    _BASE = load_base(Path(source))


def one_bootstrap(replicate: int, audit: bool = False) -> dict:
    assert _FRAME is not None and _OUTCOME is not None and _CAT is not None and _NUM is not None and _SEED is not None and _BASE is not None
    began = time.time()
    try:
        sample = make_bootstrap_frame(_FRAME, np.random.default_rng(_SEED + replicate))
        groups = sample["original_cluster_group"].astype(str).to_numpy()
        integrity = assert_group_integrity(sample, groups) if audit else None
        # Critical correction: no bootstrap copy suffix reaches crossfit.
        e, m0, m1 = _BASE.crossfit(sample, _OUTCOME, _CAT, _NUM, group_col="original_cluster_group")
        _, effect = _BASE.ingredients(sample, _OUTCOME, e, m0, m1)
        row = {"replicate": int(replicate), "rd_A0_minus_A1": float(effect["rd_A0_minus_A1"]),
               "elapsed_seconds": round(time.time() - began, 3), "n": int(len(sample)), "status": "PASS"}
        if audit:
            row.update(integrity)
        return row
    except Exception as exc:  # Failure detail is intentionally class-only; no records leave the server.
        return {"replicate": int(replicate), "rd_A0_minus_A1": np.nan, "elapsed_seconds": round(time.time() - began, 3),
                "n": np.nan, "status": f"FAIL:{type(exc).__name__}"}


def bootstrap(frame: pd.DataFrame, outcome: str, cat: list[str], num: list[str], target: int, output: Path, source: Path) -> tuple[pd.DataFrame, dict]:
    if output.exists():
        existing = pd.read_csv(output)
        existing = existing.loc[existing["replicate"].between(0, target - 1)].copy()
    else:
        existing = pd.DataFrame(columns=["replicate", "rd_A0_minus_A1", "elapsed_seconds", "n", "status"])
    # Recompute all non-PASS records; retain deterministic PASS records only.
    done = set(existing.loc[existing["status"].eq("PASS"), "replicate"].astype(int))
    rows = existing.to_dict("records")
    source_text = str(source)
    worker_init(frame, outcome, cat, num, SEED, source_text)
    serial = one_bootstrap(0, audit=True)
    if serial["status"] != "PASS":
        raise RuntimeError(f"serial replicate 0 failed: {serial}")
    ctx = mp.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=worker_init,
                                                initargs=(frame, outcome, cat, num, SEED, source_text)) as executor:
        parallel_one = executor.submit(one_bootstrap, 0, True).result()
    if parallel_one["status"] != "PASS":
        raise RuntimeError(f"one-worker replicate 0 failed: {parallel_one}")
    difference = abs(float(serial["rd_A0_minus_A1"]) - float(parallel_one["rd_A0_minus_A1"]))
    if difference > 1e-10:
        raise RuntimeError(f"serial-versus-one-worker gate failed: {difference}")
    # Fresh audit diagnostics at sentinel and dispersed replica IDs, regardless of stored state.
    diagnostic_ids = sorted(set([0, 1, 17, 113, 509, min(target - 1, 999)]))
    diagnostics = []
    for rep in diagnostic_ids:
        if rep >= 0:
            diagnostics.append(one_bootstrap(rep, audit=True))
    if any(row["status"] != "PASS" or row.get("groups_in_multiple_folds") != 0 for row in diagnostics):
        raise RuntimeError("group-leakage diagnostic failed")
    # Replicate 0 is always replaced by a fresh verified row.
    rows = [row for row in rows if int(row["replicate"]) != 0]
    rows.append({key: serial[key] for key in ("replicate", "rd_A0_minus_A1", "elapsed_seconds", "n", "status")})
    done.discard(0); done.add(0)
    # Persist the verified sentinel row even when target=1 leaves no parallel
    # batches.  This makes the small gate run exercise the identical readback
    # path used after the 1,000-replicate formal run.
    initial = pd.DataFrame(rows).drop_duplicates("replicate", keep="last").sort_values("replicate")
    atomic_csv(initial, output)
    remaining = [rep for rep in range(target) if rep not in done]
    for start in range(0, len(remaining), 8):
        batch = remaining[start:start + 8]
        with concurrent.futures.ProcessPoolExecutor(max_workers=len(batch), mp_context=ctx, initializer=worker_init,
                                                    initargs=(frame, outcome, cat, num, SEED, source_text)) as executor:
            completed = [future.result() for future in [executor.submit(one_bootstrap, rep, False) for rep in batch]]
        rows.extend(completed)
        frame_out = pd.DataFrame(rows).drop_duplicates("replicate", keep="last").sort_values("replicate")
        atomic_csv(frame_out, output)
        valid = frame_out.loc[frame_out["status"].eq("PASS"), "rd_A0_minus_A1"].dropna()
        log(f"bootstrap {int(frame_out['replicate'].nunique())}/{target}; valid={len(valid)}; median={valid.median():.8f}")
    finished = pd.read_csv(output).sort_values("replicate")
    meta = {"seed": SEED, "target": target, "serial_rd_replicate0": float(serial["rd_A0_minus_A1"]),
            "one_worker_rd_replicate0": float(parallel_one["rd_A0_minus_A1"]), "absolute_difference": difference,
            "integrity_diagnostics": diagnostics, "workers": 8, "threads_per_worker": 1, "threads_cap": 8}
    return finished, meta


def missingness_audit(frame: pd.DataFrame, cat: list[str], num: list[str]) -> tuple[pd.DataFrame, dict]:
    unknown_text = {"MISSING", "UNKNOWN", "UNK", "UNKN", "NA", "N/A", "NULL", "NONE", "."}
    rows: list[dict] = []
    strict = [(column, "categorical") for column in cat] + [(column, "numeric") for column in num]
    for column, kind in strict:
        raw = frame[column]
        numeric = pd.to_numeric(raw, errors="coerce")
        null = raw.isna()
        text = raw.astype("string").str.strip().str.upper()
        negative = raw.notna() & numeric.lt(0).fillna(False)
        if kind == "categorical":
            unknown = raw.notna() & text.isin(unknown_text).fillna(False)
            nonparseable = pd.Series(False, index=raw.index)
        else:
            unknown = pd.Series(False, index=raw.index)
            nonparseable = raw.notna() & numeric.isna()
        recode = (negative | unknown | nonparseable) & ~null
        for arm in (0, 1):
            mask = frame["A"].eq(arm)
            denominator = int(mask.sum())
            raw_null = int((null & mask).sum())
            recode_n = int((recode & mask).sum())
            negative_n = int((negative & mask).sum())
            unknown_n = int((unknown & mask).sum())
            nonparseable_n = int((nonparseable & mask).sum())
            rows.append({"variable": column, "variable_type": kind, "A": arm, "n": denominator,
                         "raw_null_n": censored(raw_null), "raw_null_percent": display_percent(raw_null, denominator),
                         "sentinel_unknown_negative_recode_n": censored(recode_n), "sentinel_unknown_negative_recode_percent": display_percent(recode_n, denominator),
                         "negative_component_n": censored(negative_n), "unknown_text_component_n": censored(unknown_n),
                         "nonparseable_component_n": censored(nonparseable_n),
                         "total_preprocess_n": censored(raw_null + recode_n), "total_preprocess_percent": display_percent(raw_null + recode_n, denominator),
                         "small_count_suppressed": bool((0 < raw_null < SMALL_CELL) or (0 < recode_n < SMALL_CELL) or (0 < raw_null + recode_n < SMALL_CELL))})
    table = pd.DataFrame(rows)
    rules = {"raw_null": "raw value is null/NA before any preprocessing",
             "categorical_recode": "non-null raw categorical value whose numeric coercion is <0, or whose stripped uppercase text is one of MISSING, UNKNOWN, UNK, UNKN, NA, N/A, NULL, NONE, .",
             "numeric_recode": "non-null raw numeric value whose numeric coercion is <0 or fails numeric coercion; negative values include common negative sentinels and are not imputed differently",
             "reconciliation": "raw null and sentinel/unknown/negative recode are disjoint by construction; total_preprocess_n equals their sum",
             "small_cell": "For count 1-10, count is <11 and corresponding percentage is withheld (null)."}
    return table, rules


def mc_limits(valid: np.ndarray) -> dict:
    random = np.random.default_rng(SEED + 700000)
    resampled = np.empty((1000, 2), dtype=float)
    for i in range(len(resampled)):
        resampled[i] = np.quantile(random.choice(valid, size=len(valid), replace=True), [0.025, 0.975])
    return {"valid_reps": int(len(valid)), "ci_low": float(np.quantile(valid, 0.025)), "ci_high": float(np.quantile(valid, 0.975)),
            "mc_endpoint_se_low": float(resampled[:, 0].std(ddof=1)), "mc_endpoint_se_high": float(resampled[:, 1].std(ddof=1)), "mc_resamples": 1000}


def privacy_audit(output_dir: Path) -> dict:
    prohibited = {"index_id", "key_nrd", "nrd_visitlink", "hospital_cluster", "original_cluster_group", "bootstrap_group"}
    rows = []
    for file in sorted(output_dir.glob("*.csv")):
        if "server_only" in file.name:
            continue
        data = pd.read_csv(file)
        bad = [column for column in data.columns if column.lower() in prohibited]
        rows.append({"file": file.name, "identifier_columns": bad, "pass": not bad})
    return {"status": "PASS" if all(row["pass"] for row in rows) else "FAIL", "patient_level_exported": False, "files": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--old-output", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", type=int, default=1000)
    arguments = parser.parse_args()
    if not 1 <= arguments.target <= 1000:
        raise ValueError("target must be 1..1000")
    output = arguments.out.resolve(); output.mkdir(parents=True, exist_ok=True)
    base = load_base(arguments.source.resolve())
    frame = add_quarter(pd.read_parquet(arguments.input.resolve()))
    cat, num = base.get_features(frame, "strict_pre_admission_proxy")
    expected_covariates = list(base.CAT) + list(base.PRIOR_NUM)
    if cat + num != expected_covariates:
        raise RuntimeError("strict covariate set differs from frozen reanalysis manifest")
    required = ["A", "DISCWT", "stratum_id", "hospital_cluster", base.PRIMARY, *expected_covariates]
    absent = [column for column in required if column not in frame]
    if absent:
        raise RuntimeError("missing frozen fields: " + ", ".join(absent))
    # The un-resampled strict point estimate must be exactly the frozen point model.
    frozen_primary = pd.read_csv(arguments.old_output / "primary_revised.csv").loc[lambda x: x["feature_set"].eq("strict_pre_admission_proxy")].iloc[0]
    point, _ = base.model_result(frame, base.PRIMARY, "strict_pre_admission_proxy", fixed_reps=10)
    point_delta = abs(float(point["rd_A0_minus_A1"]) - float(frozen_primary["rd_A0_minus_A1"]))
    if point_delta > 1e-10:
        raise RuntimeError(f"frozen point estimate changed unexpectedly: {point_delta}")
    missing, rules = missingness_audit(frame, cat, num)
    atomic_csv(missing, output / "strict_covariate_missingness_audit.csv")
    atomic_json({"status": "PASS", "strict_covariates": expected_covariates, "rules": rules,
                 "n_rows": int(len(frame)), "n_A0": int((frame.A == 0).sum()), "n_A1": int((frame.A == 1).sum())}, output / "missingness_audit_rules.json")
    trace, boot_meta = bootstrap(frame, base.PRIMARY, cat, num, arguments.target, output / "corrected_full_refit_bootstrap_server_only.csv", arguments.source.resolve())
    ids = trace["replicate"].astype(int).tolist()
    valid = trace.loc[trace["status"].eq("PASS"), "rd_A0_minus_A1"].dropna().to_numpy(float)
    if sorted(ids) != list(range(arguments.target)) or len(set(ids)) != arguments.target:
        raise RuntimeError("bootstrap IDs are not exactly 0..target-1")
    if len(valid) < math.ceil(arguments.target * 0.95):
        raise RuntimeError(f"insufficient valid corrected replicates: {len(valid)}")
    corrected = mc_limits(valid)
    old_trace = pd.read_csv(arguments.old_output / "full_refit_bootstrap_strict_server_only.csv")
    old_valid = old_trace.loc[old_trace["status"].eq("PASS"), "rd_A0_minus_A1"].dropna().to_numpy(float)
    old_ci = {"ci_low": float(np.quantile(old_valid, .025)), "ci_high": float(np.quantile(old_valid, .975)), "valid_reps": int(len(old_valid))} if len(old_valid) else {"ci_low": None, "ci_high": None, "valid_reps": 0}
    summary = {"status": "PASS", "analysis_id": "bjs-corrected-bootstrap-v1", "timestamp": stamp(),
               "input": {"path": str(arguments.input), "sha256": digest(arguments.input), "n": int(len(frame))},
               "source_code": {"path": str(arguments.source), "sha256": digest(arguments.source)},
               "strict_covariates": expected_covariates, "primary_outcome": base.PRIMARY,
               "point_estimate": {"rd_A0_minus_A1": float(point["rd_A0_minus_A1"]), "frozen_rd_A0_minus_A1": float(frozen_primary["rd_A0_minus_A1"]), "absolute_difference": point_delta,
                                  "risk_A0": float(point["risk_A0"]), "risk_A1": float(point["risk_A1"]), "events_A0": point["events_A0"], "events_A1": point["events_A1"]},
               "bootstrap": boot_meta, "canonical_primary_ci": {"method": "corrected full-nuisance-refit within-stratum hospital-cluster percentile bootstrap", **corrected,
                                                                  "rule": "Derived only from corrected copies-kept-together grouped cross-fit replicates."},
               "old_flawed_ci_audit_only": {**old_ci, "label": "Audit-only historical output. It is not used for canonical inference because duplicated hospital copies were separable across folds."},
               "workers": 8, "threads_per_worker": 1, "threads_cap": 8, "platform": platform.platform(), "python": sys.version,
               "patient_level_exported": False, "small_cell_rule": "1-10 replaced by <11 and corresponding percent withheld in aggregate missingness outputs."}
    atomic_json(summary, output / "corrected_bootstrap_summary.json")
    atomic_csv(pd.DataFrame([{**corrected, "method": "corrected full-refit grouped bootstrap", "canonical": True},
                             {**old_ci, "mc_endpoint_se_low": None, "mc_endpoint_se_high": None, "mc_resamples": None, "method": "old leakage-affected bootstrap audit only", "canonical": False}]), output / "bootstrap_ci_comparison_audit.csv")
    privacy = privacy_audit(output)
    if privacy["status"] != "PASS":
        raise RuntimeError("aggregate export privacy audit failed")
    atomic_json(privacy, output / "privacy_audit.json")
    logs = {"status": "PASS", "trace_server_only": "corrected_full_refit_bootstrap_server_only.csv", "valid_reps": int(len(valid)),
            "failed_reps": trace.loc[~trace.status.eq("PASS"), ["replicate", "status"]].to_dict("records"), "finished": stamp()}
    atomic_json(logs, output / "run_log_summary.json")
    hashes = {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    (output / "SHA256SUMS.txt").write_text("".join(f"{value}  {name}\n" for name, value in hashes.items()), encoding="utf-8")
    log("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
