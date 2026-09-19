#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.core.common
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, SplineTransformer, StandardScaler

pandas.core.common.SettingWithCopyWarning = pandas.errors.SettingWithCopyWarning
from comorbidipy import comorbidity, hfrs  # noqa: E402


SEED = 20260911
SMALL_CELL = 11
CPI_U = {2018: 251.107, 2019: 255.657, 2020: 258.811, 2021: 270.970, 2022: 292.655}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        Path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        frame.to_csv(name, index=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def load_cohort(root: Path, cohort: str = "main") -> pd.DataFrame:
    paths = [root / "03_etl" / f"year={year}" / f"{cohort}_cohort.parquet" for year in range(2018, 2023)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing cohort files: " + ", ".join(missing))
    return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


def derive_comorbidity(df: pd.DataFrame) -> pd.DataFrame:
    dx_cols = [f"DX{i}" for i in range(2, 41)]
    long = df[["index_id", "AGE", *dx_cols]].melt(
        id_vars=["index_id", "AGE"], value_vars=dx_cols, value_name="code"
    )[["index_id", "AGE", "code"]]
    long["code"] = long["code"].fillna("").astype(str).str.upper().str.replace(".", "", regex=False).str.strip()
    long = long.loc[long["code"].ne("")].drop_duplicates(["index_id", "code"])
    # comorbidipy 0.5.0 internally assumes the literal identifier column name
    # ``id`` in one age-adjustment branch, even when a different ``id=`` is
    # supplied. Normalize to that name to avoid a version-specific KeyError.
    package_long = long.rename(columns={"index_id": "id", "AGE": "age"})
    char = comorbidity(
        package_long, id="id", code="code", age="age",
        score="charlson", icd="icd10", variant="quan", weighting="quan", assign0=True,
    )
    eli = comorbidity(
        package_long, id="id", code="code", age=None,
        score="elixhauser", icd="icd10", variant="quan", weighting="vw", assign0=True,
    )
    char = char.rename(columns={"id": "index_id", **{c: "cci_" + c for c in char.columns if c != "id"}})
    eli = eli.rename(columns={"id": "index_id", **{c: "eli_" + c for c in eli.columns if c != "id"}})
    older_ids = set(df.loc[df["AGE"].ge(75), "index_id"])
    older_long = package_long.loc[package_long["id"].isin(older_ids), ["id", "code"]]
    frail = hfrs(older_long, id="id", code="code") if len(older_long) else pd.DataFrame(columns=["id", "hfrs"])
    frail = frail.rename(columns={"id": "index_id"})
    out = df.merge(char, on="index_id", how="left").merge(eli, on="index_id", how="left").merge(frail, on="index_id", how="left")
    component_cols = [c for c in out.columns if c.startswith("cci_") or c.startswith("eli_")]
    out[component_cols] = out[component_cols].fillna(0)
    out["hfrs_applicable"] = out["AGE"].ge(75).astype(int)
    out.loc[out["hfrs_applicable"].eq(1), "hfrs"] = out.loc[out["hfrs_applicable"].eq(1), "hfrs"].fillna(0)
    out["hfrs_model"] = out["hfrs"].fillna(0)
    out["quarter"] = ((out["DMONTH"].fillna(1).astype(int) - 1) // 3 + 1).astype(int)
    out["facility_cost_2022"] = out["facility_cost_nominal"] * out["year"].map(lambda y: CPI_U[2022] / CPI_U[int(y)])
    wage = pd.to_numeric(out["WAGEINDEX"], errors="coerce")
    out["facility_cost_wage_index_standardized_2022"] = np.where(
        wage.gt(0), out["facility_cost_2022"] / wage, np.nan
    )
    return out


PRIMARY_ELIXHAUSER_COMPONENTS = [
    # Chronic diagnoses unlikely to be consequences of the index operation.
    # Acute-prone discharge-code measures (coagulopathy, fluid/electrolyte
    # disorders, blood-loss/deficiency anaemia, weight loss) are deliberately
    # excluded because NRD does not provide diagnosis-level POA indicators.
    "eli_aids", "eli_alcohol", "eli_carit", "eli_chf", "eli_cpd",
    "eli_depre", "eli_diabc", "eli_diabunc", "eli_drug", "eli_hypc",
    "eli_hypothy", "eli_hypunc", "eli_ld", "eli_lymph", "eli_metacanc",
    "eli_obes", "eli_ond", "eli_para", "eli_pcd", "eli_psycho",
    "eli_pud", "eli_pvd", "eli_rf", "eli_rheumd", "eli_solidtum",
    "eli_valv",
]


def feature_columns(df: pd.DataFrame, mode: str = "primary") -> tuple[list[str], list[str]]:
    categorical = [
        "FEMALE", "PAY1", "ZIPINC_QRTL", "PL_NCHS", "RESIDENT", "HCUP_ED",
        "ELECTIVE", "AWEEKEND", "HOSP_BEDSIZE", "HOSP_URCAT4", "HOSP_UR_TEACH",
        "H_CONTRL", "year", "quarter",
    ]
    core_numeric = [
        "AGE", "prior_admissions_90d", "prior_biliary_90d", "prior_pancreatitis_90d",
        "complete_90d_lookback_proxy", "choledocholithiasis_any_dx_prefix",
        "cholangitis_any_dx_prefix", "bile_duct_obstruction_any_dx_prefix",
        "cholecystitis_any_dx_prefix",
    ]
    if mode == "primary":
        elix_components = [c for c in PRIMARY_ELIXHAUSER_COMPONENTS if c in df.columns]
    elif mode == "full_comorbidity_hfrs":
        elix_components = [
            c for c in df.columns
            if c.startswith("eli_") and c not in {"eli_comorbidity_score"}
        ]
        core_numeric += ["hfrs_model", "hfrs_applicable"]
    else:
        raise ValueError(f"unknown feature mode: {mode}")
    numeric = core_numeric + sorted(elix_components)
    return categorical, numeric


def normalize_categories(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        values = out[col]
        missing = values.isna() | (pd.to_numeric(values, errors="coerce") < 0)
        out[col] = values.astype("string")
        out.loc[missing, col] = "MISSING"
    return out


def make_model(categorical: list[str], numeric: list[str]) -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "age",
                Pipeline([
                    ("impute", SimpleImputer(strategy="median")),
                    ("spline", SplineTransformer(n_knots=4, degree=3, include_bias=False)),
                    ("scale", StandardScaler()),
                ]),
                ["AGE"],
            ),
            (
                "num",
                Pipeline([
                    ("impute", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                ]),
                [x for x in numeric if x != "AGE"],
            ),
            (
                "cat",
                Pipeline([
                    ("impute", SimpleImputer(strategy="most_frequent")),
                    ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=20)),
                ]),
                categorical,
            ),
        ],
        remainder="drop",
    )
    return Pipeline([
        ("pre", pre),
        ("model", LogisticRegression(C=1.0, max_iter=1500, solver="lbfgs", random_state=SEED)),
    ])


def fit_predict_binary(model: Pipeline, x_train: pd.DataFrame, y_train: np.ndarray, w_train: np.ndarray, x_test: pd.DataFrame) -> np.ndarray:
    if np.unique(y_train).size < 2:
        return np.full(len(x_test), np.average(y_train, weights=w_train))
    fitted = clone(model)
    fitted.fit(x_train, y_train, model__sample_weight=w_train)
    return fitted.predict_proba(x_test)[:, 1]


def crossfit_nuisance(
    frame: pd.DataFrame,
    outcome: str,
    categorical: list[str],
    numeric: list[str],
    e_fixed: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = normalize_categories(frame[categorical + numeric], categorical)
    A = frame["A"].to_numpy(dtype=int)
    y = frame[outcome].to_numpy(dtype=int)
    sw = frame["DISCWT"].fillna(1).to_numpy(float)
    sw = sw / np.mean(sw)
    cluster = frame["hospital_cluster"]
    groups = cluster.where(cluster.notna(), "missing:" + frame["index_id"].astype(str)).astype(str).to_numpy()
    unique_groups = np.unique(groups)
    folds = min(5, len(unique_groups))
    if folds < 2:
        raise RuntimeError("cross-fitting requires at least two hospital clusters")
    splitter = GroupKFold(n_splits=folds)
    model = make_model(categorical, numeric)
    e = np.zeros(len(frame), dtype=float) if e_fixed is None else e_fixed.copy()
    m0 = np.zeros(len(frame), dtype=float)
    m1 = np.zeros(len(frame), dtype=float)
    for train, test in splitter.split(x, A, groups):
        if e_fixed is None:
            e[test] = fit_predict_binary(model, x.iloc[train], A[train], sw[train], x.iloc[test])
        for arm, target in ((0, m0), (1, m1)):
            arm_train = train[A[train] == arm]
            target[test] = fit_predict_binary(model, x.iloc[arm_train], y[arm_train], sw[arm_train], x.iloc[test])
    return np.clip(e, 1e-4, 1 - 1e-4), np.clip(m0, 1e-6, 1 - 1e-6), np.clip(m1, 1e-6, 1 - 1e-6)


def bootstrap_overlap(
    frame: pd.DataFrame,
    ingredients: pd.DataFrame,
    reps: int,
    seed: int,
) -> tuple[float, float, int]:
    clustered = pd.concat(
        [frame[["stratum_id", "hospital_cluster"]].reset_index(drop=True), ingredients.reset_index(drop=True)],
        axis=1,
    ).groupby(["stratum_id", "hospital_cluster"], dropna=False, sort=False).sum(numeric_only=True).reset_index()
    strata = [
        group[["num0", "num1", "den"]].to_numpy(float)
        for _, group in clustered.groupby("stratum_id", sort=False)
    ]
    rng = np.random.default_rng(seed)
    totals = np.zeros((reps, 3), dtype=float)
    for group in strata:
        n_cluster = len(group)
        # A multinomial count vector is exactly equivalent to drawing
        # n_cluster hospitals with replacement within the stratum, but avoids
        # repeated dataframe indexing in every replicate.
        counts = rng.multinomial(n_cluster, np.full(n_cluster, 1.0 / n_cluster), size=reps)
        totals += counts @ group
    valid_mask = totals[:, 2] > 0
    estimates = totals[valid_mask, 0] / totals[valid_mask, 2] - totals[valid_mask, 1] / totals[valid_mask, 2]
    minimum_valid = max(1, int(math.ceil(reps * 0.9)))
    if estimates.size < minimum_valid:
        raise RuntimeError("insufficient valid cluster bootstrap replicates")
    return float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975)), int(estimates.size)


@dataclass
class EffectResult:
    outcome: str
    n: int
    events_A0: int
    events_A1: int
    risk_A0: float
    risk_A1: float
    rd_A0_minus_A1: float
    rr_A0_over_A1: float
    ci_low: float
    ci_high: float
    bootstrap_reps: int
    ess_A0: float
    ess_A1: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def overlap_effect(
    frame: pd.DataFrame,
    outcome: str,
    categorical: list[str],
    numeric: list[str],
    reps: int,
    e_fixed: np.ndarray | None = None,
) -> tuple[EffectResult, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    e, m0, m1 = crossfit_nuisance(frame, outcome, categorical, numeric, e_fixed=e_fixed)
    A = frame["A"].to_numpy(int)
    y = frame[outcome].to_numpy(float)
    sw = frame["DISCWT"].fillna(1).to_numpy(float)
    sw = sw / np.mean(sw)
    h = e * (1 - e)
    den = sw * h
    num1 = sw * (h * m1 + A * (1 - e) * (y - m1))
    num0 = sw * (h * m0 + (1 - A) * e * (y - m0))
    risk1, risk0 = num1.sum() / den.sum(), num0.sum() / den.sum()
    ingredients = pd.DataFrame({"num0": num0, "num1": num1, "den": den})
    low, high, valid = bootstrap_overlap(frame, ingredients, reps, SEED + len(frame) + int(y.sum()))
    ow = sw * np.where(A == 1, 1 - e, e)
    ess0 = ow[A == 0].sum() ** 2 / np.square(ow[A == 0]).sum()
    ess1 = ow[A == 1].sum() ** 2 / np.square(ow[A == 1]).sum()
    result = EffectResult(
        outcome=outcome,
        n=len(frame),
        events_A0=int(y[A == 0].sum()),
        events_A1=int(y[A == 1].sum()),
        risk_A0=float(risk0),
        risk_A1=float(risk1),
        rd_A0_minus_A1=float(risk0 - risk1),
        rr_A0_over_A1=float(risk0 / risk1) if risk1 > 0 else math.inf,
        ci_low=low,
        ci_high=high,
        bootstrap_reps=valid,
        ess_A0=float(ess0),
        ess_A1=float(ess1),
    )
    return result, e, m0, m1, ow


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    return float(np.sum(values * weights) / np.sum(weights))


def weighted_quantile(values: np.ndarray, weights: np.ndarray, probability: float) -> float:
    order = np.argsort(values)
    ordered_values = values[order]
    ordered_weights = weights[order]
    cumulative = np.cumsum(ordered_weights) - 0.5 * ordered_weights
    cumulative = cumulative / ordered_weights.sum()
    return float(np.interp(probability, cumulative, ordered_values))


def progress(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def balance_table(frame: pd.DataFrame, categorical: list[str], numeric: list[str], weights: np.ndarray) -> pd.DataFrame:
    A = frame["A"].to_numpy(int)
    rows = []
    for name in numeric:
        x = pd.to_numeric(frame[name], errors="coerce").fillna(pd.to_numeric(frame[name], errors="coerce").median()).to_numpy(float)
        means = [weighted_mean(x[A == arm], weights[A == arm]) for arm in (0, 1)]
        vars_ = [weighted_mean(np.square(x[A == arm] - means[arm]), weights[A == arm]) for arm in (0, 1)]
        denom = math.sqrt((vars_[0] + vars_[1]) / 2) if vars_[0] + vars_[1] > 0 else 1
        rows.append({"variable": name, "level": "continuous", "weighted_A0": means[0], "weighted_A1": means[1], "smd": (means[1] - means[0]) / denom})
    cats = normalize_categories(frame[categorical], categorical)
    for name in categorical:
        for level in sorted(cats[name].astype(str).unique()):
            x = cats[name].astype(str).eq(level).to_numpy(float)
            p0, p1 = weighted_mean(x[A == 0], weights[A == 0]), weighted_mean(x[A == 1], weights[A == 1])
            denom = math.sqrt((p0 * (1 - p0) + p1 * (1 - p1)) / 2) or 1
            rows.append({"variable": name, "level": level, "weighted_A0": p0, "weighted_A1": p1, "smd": (p1 - p0) / denom})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument(
        "--secondary-bootstrap",
        type=int,
        default=None,
        help="Override secondary/sensitivity bootstrap count; default is max(500, primary/2).",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    secondary_bootstrap = args.secondary_bootstrap if args.secondary_bootstrap is not None else max(500, args.bootstrap // 2)
    if args.bootstrap < 1 or secondary_bootstrap < 1:
        raise ValueError("bootstrap counts must be positive")
    started = time.time()
    progress("loading main cohort")
    raw = load_cohort(root, "main")
    raw["any_J96"] = raw[[f"DX{i}" for i in range(1, 41)]].fillna("").astype(str).apply(
        lambda col: col.str.upper().str.replace(".", "", regex=False).str.startswith("J96")
    ).any(axis=1).astype(int)
    progress("deriving harmonized comorbidity and frailty measures")
    cohort = derive_comorbidity(raw)
    categorical, numeric = feature_columns(cohort)
    ready_path = root / "03_etl" / "analysis_ready_main.parquet"
    pq.write_table(pa.Table.from_pandas(cohort.drop(columns=[f"DX{i}" for i in range(1, 41)]), preserve_index=False), ready_path, compression="zstd")

    progress("fitting primary cross-fitted overlap AIPW model")
    primary, e, m0, m1, ow = overlap_effect(
        cohort, "y90_primary_middle", categorical, numeric, args.bootstrap,
    )
    nuisance_path = root / "05_models" / "ate" / "nuisance_main_server_only.parquet"
    pq.write_table(
        pa.Table.from_pandas(
            pd.DataFrame({
                "index_id": cohort["index_id"], "e_hat": e, "m0_hat": m0,
                "m1_hat": m1, "overlap_weight": ow,
            }),
            preserve_index=False,
        ),
        nuisance_path,
        compression="zstd",
    )
    effects = [primary.as_dict()]
    secondary_names = [
        "y30_primary_middle", "y60_primary_middle", "y90_narrow_principal", "y90_wide_any_dx",
        "y90_all_cause", "y90_principal_k85", "y90_principal_k851",
        "y90_subsequent_cholecystectomy", "y90_subsequent_ercp_related",
    ]
    for name in secondary_names:
        progress(f"fitting secondary outcome: {name}")
        result, *_ = overlap_effect(cohort, name, categorical, numeric, secondary_bootstrap, e_fixed=e)
        effects.append(result.as_dict())
    effect_frame = pd.DataFrame(effects)
    publish_effects = effect_frame.copy()
    for column in ("events_A0", "events_A1"):
        publish_effects[column] = publish_effects[column].map(lambda x: "<11" if 0 <= int(x) < SMALL_CELL else int(x))
    atomic_csv(root / "06_results" / "table2_primary_and_secondary_effects.csv", publish_effects)

    balance = balance_table(cohort, categorical, numeric, ow)
    atomic_csv(root / "06_results" / "table1_weighted_balance.csv", balance)
    max_smd = float(balance["smd"].abs().max())
    core_max = float(balance.loc[balance["variable"].isin(["AGE", "choledocholithiasis_any_dx_prefix", "cholangitis_any_dx_prefix"]), "smd"].abs().max())

    diagnostics = {
        "n": len(cohort),
        "A0": int((cohort["A"] == 0).sum()),
        "A1": int((cohort["A"] == 1).sum()),
        "event_A0": primary.events_A0,
        "event_A1": primary.events_A1,
        "ps_quantiles_A0": np.quantile(e[cohort["A"].eq(0)], [0, 0.01, 0.05, 0.5, 0.95, 0.99, 1]).tolist(),
        "ps_quantiles_A1": np.quantile(e[cohort["A"].eq(1)], [0, 0.01, 0.05, 0.5, 0.95, 0.99, 1]).tolist(),
        "max_abs_smd": max_smd,
        "core_max_abs_smd": core_max,
        "pct_balance_below_0_05": float((balance["smd"].abs() < 0.05).mean()),
        "all_balance_below_0_10": bool((balance["smd"].abs() < 0.10).all()),
        "ess_A0": primary.ess_A0,
        "ess_A1": primary.ess_A1,
        "ess_ratio_A0": primary.ess_A0 / max(1, (cohort["A"] == 0).sum()),
        "ess_ratio_A1": primary.ess_A1 / max(1, (cohort["A"] == 1).sum()),
    }
    atomic_json(root / "05_models" / "ate" / "overlap_diagnostics.json", diagnostics)

    sensitivity_specs = {
        "complete_lookback_proxy": cohort["complete_90d_lookback_proxy"].eq(1),
        "exclude_2020": cohort["year"].ne(2020),
        "valid_chole_prday_if_A1": cohort["A"].eq(0) | cohort["chole_prday_all_missing_or_invalid"].eq(0),
        "exclude_all_J96": cohort["any_J96"].eq(0),
        "aprdrg_severity_1_or_2": cohort["APRDRG_Severity"].isin([1, 2]),
        "development_2018_2020": cohort["year"].le(2020),
        "temporal_validation_2021_2022": cohort["year"].ge(2021),
    }
    sensitivity = []
    for label, mask in sensitivity_specs.items():
        progress(f"fitting sensitivity analysis: {label}")
        subset = cohort.loc[mask].reset_index(drop=True)
        result, *_ = overlap_effect(subset, "y90_primary_middle", *feature_columns(subset), secondary_bootstrap)
        item = result.as_dict()
        item["analysis"] = label
        sensitivity.append(item)
    for omitted in range(2018, 2023):
        progress(f"fitting sensitivity analysis: leave_out_{omitted}")
        subset = cohort.loc[cohort["year"].ne(omitted)].reset_index(drop=True)
        result, *_ = overlap_effect(subset, "y90_primary_middle", *feature_columns(subset), secondary_bootstrap)
        item = result.as_dict()
        item["analysis"] = f"leave_out_{omitted}"
        sensitivity.append(item)
    progress("fitting sensitivity analysis: include_gap0")
    gap0 = cohort.copy()
    gap0["y90_primary_gap0"] = gap0["y90_primary_middle_gap0"]
    result, *_ = overlap_effect(gap0, "y90_primary_gap0", categorical, numeric, secondary_bootstrap, e_fixed=e)
    item = result.as_dict()
    item["analysis"] = "include_gap0"
    sensitivity.append(item)
    progress("fitting sensitivity analysis: full_elixhauser_plus_hfrs_discharge_codes")
    full_result, *_ = overlap_effect(
        cohort,
        "y90_primary_middle",
        *feature_columns(cohort, mode="full_comorbidity_hfrs"),
        secondary_bootstrap,
    )
    full_item = full_result.as_dict()
    full_item["analysis"] = "full_elixhauser_plus_hfrs_discharge_codes"
    sensitivity.append(full_item)
    atomic_csv(root / "06_results" / "sensitivity_matrix.csv", pd.DataFrame(sensitivity))

    progress("fitting broad K85.1* cohort sensitivity analysis")
    broad_raw = load_cohort(root, "broad")
    broad = derive_comorbidity(broad_raw)
    broad_result, *_ = overlap_effect(
        broad, "y90_primary_middle", *feature_columns(broad), secondary_bootstrap
    )
    broad_row = broad_result.as_dict()
    broad_row["analysis"] = "broad_K851_star_cohort"
    sensitivity.append(broad_row)
    atomic_csv(root / "06_results" / "sensitivity_matrix.csv", pd.DataFrame(sensitivity))

    resource_rows = []
    for arm in (0, 1):
        mask = cohort["A"].eq(arm).to_numpy()
        weights = ow[mask]
        for outcome in ("LOS", "facility_cost_2022", "facility_cost_wage_index_standardized_2022"):
            values = pd.to_numeric(cohort.loc[mask, outcome], errors="coerce").to_numpy(float)
            keep = np.isfinite(values)
            resource_rows.append({
                "A": arm,
                "outcome": outcome,
                "n_nonmissing": int(keep.sum()),
                "weighted_mean": weighted_mean(values[keep], weights[keep]),
                "weighted_median": weighted_quantile(values[keep], weights[keep], 0.50),
                "weighted_q25": weighted_quantile(values[keep], weights[keep], 0.25),
                "weighted_q75": weighted_quantile(values[keep], weights[keep], 0.75),
                "interpretation_label": "associational_post_treatment_resource_outcome",
            })
    resource_frame = pd.DataFrame(resource_rows)
    atomic_csv(root / "06_results" / "cost_and_los_associational.csv", resource_frame)

    harm = load_cohort(root, "harm")
    harm_rows = []
    for arm in (0, 1):
        group = harm.loc[harm["A"].eq(arm)]
        deaths = int(group["DIED"].eq(1).sum())
        harm_rows.append({
            "A": arm,
            "n": len(group),
            "in_hospital_deaths": "<11" if deaths < SMALL_CELL else deaths,
            "weighted_death_risk_descriptive": (
                "<11" if deaths < SMALL_CELL else float(np.average(group["DIED"].eq(1), weights=group["DISCWT"].fillna(1)))
            ),
            "los_median": float(group["LOS"].median()),
            "interpretation_label": "descriptive_not_causal",
        })
    atomic_csv(root / "06_results" / "harm_descriptive.csv", pd.DataFrame(harm_rows))

    model_manifest = {
        "analysis_version": "nrd_analysis_v1.0.0",
        "seed": SEED,
        "estimand": "NRD-design-weighted overlap-population A0 minus A1 90-day absolute risk difference",
        "nuisance": "5-fold hospital-grouped cross-fitted logistic regression with age splines and one-hot categorical variables",
        "primary_covariate_policy": "Admission/demographic/hospital/prior-utilization/biliary features plus chronic Elixhauser components; HFRS and acute-prone discharge-code components excluded because diagnosis-level POA is unavailable.",
        "bootstrap": "stratified hospital-cluster nonparametric bootstrap of AIPW ratio ingredients",
        "bootstrap_reps": {"primary": args.bootstrap, "secondary_and_sensitivity": secondary_bootstrap},
        "primary": primary.as_dict(),
        "diagnostics": diagnostics,
        "input_hashes": {str(path.relative_to(root)): sha256(path) for path in sorted((root / "03_etl").glob("year=*/main_cohort.parquet"))},
        "analysis_ready_hash": sha256(ready_path),
        "nuisance_hash": sha256(nuisance_path),
        "elapsed_seconds": round(time.time() - started, 3),
        "post_treatment_blacklist_enforced": True,
        "blacklisted_columns": ["APRDRG_Severity", "APRDRG_Risk_Mortality", "LOS", "TOTCHG", "DISPUNIFORM", "REHABTRANSFER"],
    }
    atomic_json(root / "05_models" / "model_manifest.json", model_manifest)
    progress("analysis complete")
    print(json.dumps({"status": "PASS", "primary": primary.as_dict(), "diagnostics": diagnostics}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
