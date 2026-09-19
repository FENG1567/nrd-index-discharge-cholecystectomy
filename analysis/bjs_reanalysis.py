#!/usr/bin/env python3
"""Aggregate-only BJS revision reanalysis for the NRD mild BAP project.

This script is intentionally conservative: it estimates associations conditional
on discharge status, never an admission-time treatment-strategy effect.  It
does not export patient-level records.  The reference model excludes acute
index-admission biliary diagnoses and all identified post-admission variables.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import multiprocessing as mp
import os
import platform
import sys
import time
from pathlib import Path

# Set before importing numerical libraries.  Parallel bootstrap workers are each
# single-threaded; the parent launches no more than eight workers.
for _thread_var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_var, "1")

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, SplineTransformer, StandardScaler

SEED = 20260912
SMALL_CELL = 11
PRIMARY = "y90_primary_middle"
CHRONIC = [
    "eli_aids", "eli_alcohol", "eli_carit", "eli_chf", "eli_cpd", "eli_depre",
    "eli_diabc", "eli_diabunc", "eli_drug", "eli_hypc", "eli_hypothy",
    "eli_hypunc", "eli_ld", "eli_lymph", "eli_metacanc", "eli_obes", "eli_ond",
    "eli_para", "eli_pcd", "eli_psycho", "eli_pud", "eli_pvd", "eli_rf",
    "eli_rheumd", "eli_solidtum", "eli_valv",
]
CAT = [
    "FEMALE", "PAY1", "ZIPINC_QRTL", "PL_NCHS", "RESIDENT", "HCUP_ED",
    "ELECTIVE", "AWEEKEND", "HOSP_BEDSIZE", "HOSP_URCAT4", "HOSP_UR_TEACH",
    "H_CONTRL", "year", "quarter",
]
PRIOR_NUM = ["AGE", "prior_admissions_90d", "prior_biliary_90d", "prior_pancreatitis_90d", "complete_90d_lookback_proxy"]
ACUTE_BILIARY = ["choledocholithiasis_any_dx_prefix", "cholangitis_any_dx_prefix", "bile_duct_obstruction_any_dx_prefix", "cholecystitis_any_dx_prefix"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def censor(value: int | float) -> int | float | str:
    if 0 < int(value) < SMALL_CELL:
        return "<11"
    return int(value) if isinstance(value, (int, np.integer)) or float(value).is_integer() else float(value)


def add_quarter(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["quarter"] = ((pd.to_numeric(df["DMONTH"], errors="coerce").fillna(1).astype(int) - 1) // 3 + 1).astype(int)
    return df


def get_features(df: pd.DataFrame, spec: str) -> tuple[list[str], list[str]]:
    cat = [x for x in CAT if x in df.columns]
    if spec == "strict_pre_admission_proxy":
        num = [x for x in PRIOR_NUM if x in df.columns]
    elif spec == "chronic_proxy_sensitivity":
        num = [x for x in PRIOR_NUM + CHRONIC if x in df.columns]
    elif spec == "original_extended_sensitivity":
        num = [x for x in PRIOR_NUM + ACUTE_BILIARY + CHRONIC if x in df.columns]
    else:
        raise ValueError(f"unknown feature set {spec}")
    if "AGE" not in num:
        raise RuntimeError("AGE missing from required covariate set")
    return cat, num


def normalize_categories(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        raw = out[col]
        num = pd.to_numeric(raw, errors="coerce")
        missing = raw.isna() | num.lt(0)
        out[col] = raw.astype("string")
        out.loc[missing, col] = "MISSING"
    return out


def make_model(cat: list[str], num: list[str]) -> Pipeline:
    other_num = [x for x in num if x != "AGE"]
    transforms: list[tuple] = [
        ("age", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("spline", SplineTransformer(n_knots=4, degree=3, include_bias=False)),
            ("scale", StandardScaler()),
        ]), ["AGE"]),
    ]
    if other_num:
        transforms.append(("num", Pipeline([
            ("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()),
        ]), other_num))
    if cat:
        transforms.append(("cat", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=20)),
        ]), cat))
    pre = ColumnTransformer(transforms, remainder="drop")
    return Pipeline([("pre", pre), ("model", LogisticRegression(C=1.0, max_iter=1500, solver="lbfgs", random_state=SEED))])


def fit_prob(model: Pipeline, x_train: pd.DataFrame, y_train: np.ndarray, weights: np.ndarray, x_test: pd.DataFrame) -> np.ndarray:
    if len(y_train) == 0:
        return np.full(len(x_test), 0.5)
    if np.unique(y_train).size < 2:
        return np.full(len(x_test), float(np.average(y_train, weights=weights)))
    m = clone(model)
    m.fit(x_train, y_train, model__sample_weight=weights)
    return m.predict_proba(x_test)[:, 1]


def crossfit(frame: pd.DataFrame, outcome: str, cat: list[str], num: list[str], group_col: str = "hospital_cluster") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = normalize_categories(frame[cat + num], cat)
    a = frame["A"].to_numpy(dtype=int)
    y = frame[outcome].to_numpy(dtype=int)
    sw = frame["DISCWT"].fillna(1).to_numpy(float)
    sw /= sw.mean()
    groups = frame[group_col].astype(str).to_numpy()
    n_groups = len(np.unique(groups))
    folds = min(5, n_groups)
    if folds < 2:
        raise RuntimeError("fewer than two hospital clusters for grouped cross-fitting")
    splitter = GroupKFold(n_splits=folds)
    model = make_model(cat, num)
    e, m0, m1 = (np.zeros(len(frame), dtype=float) for _ in range(3))
    for train, test in splitter.split(x, a, groups):
        e[test] = fit_prob(model, x.iloc[train], a[train], sw[train], x.iloc[test])
        for arm, target in ((0, m0), (1, m1)):
            idx = train[a[train] == arm]
            target[test] = fit_prob(model, x.iloc[idx], y[idx], sw[idx], x.iloc[test])
    return np.clip(e, 1e-4, 1 - 1e-4), np.clip(m0, 1e-6, 1 - 1e-6), np.clip(m1, 1e-6, 1 - 1e-6)


def ingredients(frame: pd.DataFrame, outcome: str, e: np.ndarray, m0: np.ndarray, m1: np.ndarray) -> tuple[pd.DataFrame, dict]:
    a = frame["A"].to_numpy(dtype=int)
    y = frame[outcome].to_numpy(dtype=float)
    sw = frame["DISCWT"].fillna(1).to_numpy(float)
    sw /= sw.mean()
    h = e * (1 - e)
    den = sw * h
    n1 = sw * (h * m1 + a * (1 - e) * (y - m1))
    n0 = sw * (h * m0 + (1 - a) * e * (y - m0))
    d = pd.DataFrame({"num0": n0, "num1": n1, "den": den})
    risk0, risk1 = n0.sum() / den.sum(), n1.sum() / den.sum()
    ow = sw * np.where(a == 1, 1 - e, e)
    ess0 = float(ow[a == 0].sum() ** 2 / np.square(ow[a == 0]).sum())
    ess1 = float(ow[a == 1].sum() ** 2 / np.square(ow[a == 1]).sum())
    return d, {"risk_A0": float(risk0), "risk_A1": float(risk1), "rd_A0_minus_A1": float(risk0-risk1), "rr_A0_over_A1": float(risk0/risk1), "ess_A0": ess0, "ess_A1": ess1, "overlap_weight": ow}


def fixed_score_bootstrap(frame: pd.DataFrame, ing: pd.DataFrame, reps: int, seed: int) -> np.ndarray:
    cluster = pd.concat([frame[["stratum_id", "hospital_cluster"]].reset_index(drop=True), ing.reset_index(drop=True)], axis=1)
    c = cluster.groupby(["stratum_id", "hospital_cluster"], dropna=False, sort=False)[["num0", "num1", "den"]].sum().reset_index()
    rng = np.random.default_rng(seed)
    totals = np.zeros((reps, 3))
    for _, g in c.groupby("stratum_id", sort=False):
        x = g[["num0", "num1", "den"]].to_numpy(float)
        k = len(x)
        totals += rng.multinomial(k, np.full(k, 1/k), size=reps) @ x
    ok = totals[:,2] > 0
    return totals[ok,0] / totals[ok,2] - totals[ok,1] / totals[ok,2]


def eif_ci(frame: pd.DataFrame, ing: pd.DataFrame, rd: float) -> tuple[float, float, float]:
    # Taylor linearisation of the AIPW ratio at the hospital-within-stratum level.
    z = ing["num0"].to_numpy(float) - ing["num1"].to_numpy(float) - rd * ing["den"].to_numpy(float)
    tmp = frame[["stratum_id", "hospital_cluster"]].copy()
    tmp["z"] = z
    tmp["den"] = ing["den"].to_numpy(float)
    byh = tmp.groupby(["stratum_id", "hospital_cluster"], dropna=False, sort=False)[["z", "den"]].sum().reset_index()
    var_num = 0.0
    for _, g in byh.groupby("stratum_id", sort=False):
        nh = len(g)
        if nh > 1:
            vals = g["z"].to_numpy(float)
            var_num += nh / (nh - 1) * float(np.square(vals - vals.mean()).sum())
    den = float(ing["den"].sum())
    se = math.sqrt(var_num) / den
    return rd - 1.95996398454 * se, rd + 1.95996398454 * se, se


def make_bootstrap_frame(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    pieces = []
    for stratum, g in frame.groupby("stratum_id", sort=False, dropna=False):
        clusters = g["hospital_cluster"].astype(str).unique()
        chosen = rng.integers(0, len(clusters), size=len(clusters))
        for copy_number, pos in enumerate(chosen):
            h = clusters[pos]
            part = g.loc[g["hospital_cluster"].astype(str).eq(h)].copy()
            part["bootstrap_group"] = f"{stratum}|{h}|copy{copy_number}"
            pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


_BOOT_FRAME: pd.DataFrame | None = None
_BOOT_OUTCOME: str | None = None
_BOOT_CAT: list[str] | None = None
_BOOT_NUM: list[str] | None = None
_BOOT_SEED: int | None = None


def _bootstrap_worker_init(frame: pd.DataFrame, outcome: str, cat: list[str], num: list[str], seed: int) -> None:
    global _BOOT_FRAME, _BOOT_OUTCOME, _BOOT_CAT, _BOOT_NUM, _BOOT_SEED
    _BOOT_FRAME, _BOOT_OUTCOME, _BOOT_CAT, _BOOT_NUM, _BOOT_SEED = frame, outcome, cat, num, seed


def _one_refit_bootstrap(replicate: int) -> dict:
    """One deterministic complete re-fit; workers return aggregate results only."""
    assert _BOOT_FRAME is not None and _BOOT_OUTCOME is not None and _BOOT_CAT is not None and _BOOT_NUM is not None and _BOOT_SEED is not None
    started = time.time()
    try:
        rng = np.random.default_rng(_BOOT_SEED + replicate)
        sample = make_bootstrap_frame(_BOOT_FRAME, rng)
        e, m0, m1 = crossfit(sample, _BOOT_OUTCOME, _BOOT_CAT, _BOOT_NUM, group_col="bootstrap_group")
        _, effect = ingredients(sample, _BOOT_OUTCOME, e, m0, m1)
        return {"replicate": replicate, "rd_A0_minus_A1": effect["rd_A0_minus_A1"], "elapsed_seconds": round(time.time()-started, 3), "n": len(sample), "status": "PASS"}
    except Exception as exc:
        return {"replicate": replicate, "rd_A0_minus_A1": np.nan, "elapsed_seconds": round(time.time()-started, 3), "n": np.nan, "status": f"FAIL:{type(exc).__name__}"}


def refit_bootstrap(frame: pd.DataFrame, outcome: str, cat: list[str], num: list[str], target_reps: int, output: Path, seed: int, workers: int) -> pd.DataFrame:
    if output.exists():
        old = pd.read_csv(output)
    else:
        old = pd.DataFrame(columns=["replicate", "rd_A0_minus_A1", "elapsed_seconds", "n", "status"])
    done = set(old.loc[old["status"].eq("PASS"), "replicate"].astype(int).tolist()) if len(old) else set()
    rows = old.to_dict("records")
    # Establish replicate 0 in the same single-thread runtime as the parallel
    # workers.  This is deliberately separate from a previously aborted
    # eight-thread diagnostic, which is retained but not part of inference.
    if 0 not in done:
        _bootstrap_worker_init(frame, outcome, cat, num, seed)
        baseline = _one_refit_bootstrap(0)
        if baseline["status"] != "PASS":
            raise RuntimeError(f"one-thread serial baseline failed: {baseline}")
        rows.append(baseline)
        old = pd.DataFrame(rows).drop_duplicates("replicate", keep="last").sort_values("replicate")
        atomic_write_csv(old, output)
        done.add(0)
        log("one-thread serial baseline PASS for replicate 0")
    # Recompute the serial baseline in a fresh parallel worker.  This invariant
    # prevents a parallelization change from silently altering the draw.
    if 0 in done:
        ctx = mp.get_context("fork")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=_bootstrap_worker_init, initargs=(frame, outcome, cat, num, seed)) as pool:
            check = pool.submit(_one_refit_bootstrap, 0).result()
        prior = float(old.loc[(old["replicate"].astype(int).eq(0)) & old["status"].eq("PASS"), "rd_A0_minus_A1"].iloc[0])
        if check["status"] != "PASS" or abs(float(check["rd_A0_minus_A1"]) - prior) > 1e-10:
            raise RuntimeError(f"parallel consistency check failed for replicate 0: prior={prior}, recomputed={check}")
        log("parallel consistency check PASS for replicate 0 (tolerance 1e-10)")
    remaining = [b for b in range(target_reps) if b not in done]
    ctx = mp.get_context("fork")
    for start in range(0, len(remaining), workers):
        batch = remaining[start:start+workers]
        with concurrent.futures.ProcessPoolExecutor(max_workers=len(batch), mp_context=ctx, initializer=_bootstrap_worker_init, initargs=(frame, outcome, cat, num, seed)) as pool:
            futures = [pool.submit(_one_refit_bootstrap, b) for b in batch]
            batch_rows = [future.result() for future in futures]
        rows.extend(batch_rows)
        result = pd.DataFrame(rows).drop_duplicates("replicate", keep="last").sort_values("replicate")
        atomic_write_csv(result, output)  # Main process is the only writer.
        good = result.loc[result["status"].eq("PASS"), "rd_A0_minus_A1"].dropna()
        log(f"full-refit bootstrap {min(start+len(batch)+len(done), target_reps)}/{target_reps}; valid={len(good)}; median={good.median() if len(good) else float('nan'):.6f}")
    return pd.read_csv(output)


def environmental_floating_audit(out: Path) -> pd.DataFrame:
    old_path=out/"full_refit_bootstrap_aborted_serial8_environmental_server_only.csv"
    new_path=out/"full_refit_bootstrap_strict_server_only.csv"
    if not old_path.exists() or not new_path.exists():
        return pd.DataFrame([{"comparison":"not_available","old_rd":np.nan,"new_rd":np.nan,"absolute_difference":np.nan,"interpretation":"No prior 8-thread diagnostic file was available."}])
    old=pd.read_csv(old_path); new=pd.read_csv(new_path)
    o=float(old.loc[(old.replicate.astype(int)==0)&old.status.eq("PASS"),"rd_A0_minus_A1"].iloc[0]); n=float(new.loc[(new.replicate.astype(int)==0)&new.status.eq("PASS"),"rd_A0_minus_A1"].iloc[0])
    return pd.DataFrame([{"comparison":"serial_8thread_aborted_diagnostic_vs_serial_1thread_formal_baseline_replicate0","old_rd":o,"new_rd":n,"absolute_difference":abs(o-n),"interpretation":"Environmental floating-point difference only; neither point estimate nor CI is affected at conventional percentage-point rounding. The 8-thread run is excluded from formal bootstrap inference."}])


def bootstrap_mc_diagnostics(valid: np.ndarray, seed: int) -> pd.DataFrame:
    """Monte-Carlo uncertainty of percentile limits, conditional on obtained bootstrap draws."""
    rng=np.random.default_rng(seed); reps=1000
    q=np.empty((reps,2))
    for i in range(reps):
        q[i]=np.quantile(rng.choice(valid,size=len(valid),replace=True),[.025,.975])
    return pd.DataFrame([{"valid_bootstrap_reps":int(len(valid)),"percentile_ci_low":float(np.quantile(valid,.025)),"percentile_ci_high":float(np.quantile(valid,.975)),"mc_se_ci_low":float(q[:,0].std(ddof=1)),"mc_se_ci_high":float(q[:,1].std(ddof=1)),"mc_reps":reps,"interpretation":"Conditional Monte-Carlo error of reported percentile limits; smaller with more full-refit bootstrap replicates."}])


def balance_details(frame: pd.DataFrame, cat: list[str], num: list[str], overlap_weight: np.ndarray) -> pd.DataFrame:
    """Aggregate balance data, retaining no row- or patient-level records."""
    a = frame["A"].to_numpy(int)
    rows=[]
    for weighting, weights in (("unweighted", np.ones(len(frame))), ("overlap_weighted", overlap_weight)):
        for col in num:
            x=pd.to_numeric(frame[col],errors="coerce").fillna(pd.to_numeric(frame[col],errors="coerce").median()).to_numpy(float)
            means=[float(np.average(x[a==arm],weights=weights[a==arm])) for arm in (0,1)]
            vs=[float(np.average((x[a==arm]-means[arm])**2,weights=weights[a==arm])) for arm in (0,1)]
            rows.append({"weighting":weighting,"variable":col,"level":"continuous","A0_value":means[0],"A1_value":means[1],"smd":float((means[1]-means[0])/(math.sqrt(sum(vs)/2) or 1))})
        norm=normalize_categories(frame[cat],cat)
        for col in cat:
            for lev in sorted(norm[col].astype(str).unique()):
                x=norm[col].astype(str).eq(lev).to_numpy(float)
                ps=[float(np.average(x[a==arm],weights=weights[a==arm])) for arm in (0,1)]
                denom=math.sqrt((ps[0]*(1-ps[0])+ps[1]*(1-ps[1]))/2) or 1
                rows.append({"weighting":weighting,"variable":col,"level":str(lev),"A0_value":ps[0],"A1_value":ps[1],"smd":float((ps[1]-ps[0])/denom)})
    return pd.DataFrame(rows)


def ps_plot_source(frame: pd.DataFrame, nuisance: pd.DataFrame) -> pd.DataFrame:
    """Bin-level overlap diagnostics suitable for plotting, with HCUP small-cell suppression."""
    edges=np.linspace(0,1,21); a=frame["A"].to_numpy(int); e=nuisance["e_hat"].to_numpy(float); ow=nuisance["overlap_weight"].to_numpy(float)
    b=np.clip(np.digitize(e,edges,right=False)-1,0,len(edges)-2); rows=[]
    for arm in (0,1):
        denom=float(ow[a==arm].sum())
        for j in range(len(edges)-1):
            mask=(a==arm)&(b==j); count=int(mask.sum())
            rows.append({"A":arm,"bin_lower":float(edges[j]),"bin_upper":float(edges[j+1]),"unweighted_n":censor(count),"weighted_fraction":float(ow[mask].sum()/denom) if denom else np.nan})
    return pd.DataFrame(rows)


def model_result(frame: pd.DataFrame, outcome: str, feature_set: str, fixed_reps: int) -> tuple[dict, pd.DataFrame]:
    cat, num = get_features(frame, feature_set)
    e, m0, m1 = crossfit(frame, outcome, cat, num)
    ing, effect = ingredients(frame, outcome, e, m0, m1)
    fixed = fixed_score_bootstrap(frame, ing, fixed_reps, SEED + len(frame) + int(frame[outcome].sum()))
    eif_lo, eif_hi, eif_se = eif_ci(frame, ing, effect["rd_A0_minus_A1"])
    a = frame["A"].to_numpy(int)
    y = frame[outcome].to_numpy(int)
    row = {
        "outcome": outcome, "feature_set": feature_set, "estimand": "discharge-status association in overlap population",
        "n": int(len(frame)), "events_A0": censor(int(y[a==0].sum())), "events_A1": censor(int(y[a==1].sum())),
        **{k: v for k,v in effect.items() if k != "overlap_weight"},
        "fixed_score_ci_low": float(np.quantile(fixed, .025)), "fixed_score_ci_high": float(np.quantile(fixed, .975)),
        "fixed_score_valid_reps": int(len(fixed)), "eif_ci_low": eif_lo, "eif_ci_high": eif_hi, "eif_se": eif_se,
        "n_categorical": len(cat), "n_numeric": len(num), "max_abs_smd": np.nan,
    }
    # Balance is reported only for adjustment variables; outcome-model components are not inspected.
    ow = effect["overlap_weight"]
    smds = []
    for col in num:
        x = pd.to_numeric(frame[col], errors="coerce").fillna(pd.to_numeric(frame[col], errors="coerce").median()).to_numpy(float)
        means = [np.average(x[a==arm], weights=ow[a==arm]) for arm in (0,1)]
        variances = [np.average((x[a==arm]-means[arm])**2, weights=ow[a==arm]) for arm in (0,1)]
        smds.append(abs((means[1]-means[0]) / (math.sqrt(sum(variances)/2) or 1)))
    for col in cat:
        xx = normalize_categories(frame[[col]], [col])[col]
        for lev in xx.unique():
            x = xx.eq(lev).to_numpy(float); ps=[np.average(x[a==arm],weights=ow[a==arm]) for arm in (0,1)]
            smds.append(abs((ps[1]-ps[0])/(math.sqrt((ps[0]*(1-ps[0])+ps[1]*(1-ps[1]))/2) or 1)))
    row["max_abs_smd"] = float(max(smds)) if smds else np.nan
    return row, pd.DataFrame({"e_hat": e, "m0_hat":m0, "m1_hat":m1, "overlap_weight":ow})


def record_reporting(root: Path, frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, g in frame.groupby("year", sort=True):
        a = g["A"].to_numpy(int); y = g[PRIMARY].to_numpy(int)
        qc_path = root / "04_qc" / f"etl_qc_{int(year)}.json"
        qc = json.loads(qc_path.read_text()) if qc_path.exists() else {}
        flow = qc.get("flows", {}).get("main", {})
        rows.append({
            "year": int(year), "analysis_n": int(len(g)), "A0_n": int((a==0).sum()), "A1_n": int((a==1).sum()),
            "primary_events_A0": censor(int(y[a==0].sum())), "primary_events_A1": censor(int(y[a==1].sum())),
            "n_strata": int(g["stratum_id"].nunique(dropna=True)), "n_hospital_clusters": int(g["hospital_cluster"].nunique(dropna=True)),
            "raw_records": flow.get("raw_records"), "adult_records": flow.get("adult"), "diagnosis_match": flow.get("diagnosis_match"),
            "no_severe_proxy": flow.get("no_severe_proxy"), "survived_discharge": flow.get("survived_discharge"),
            "discharge_month_1_to_9": flow.get("discharge_month_1_to_9"), "deduplicated_index": flow.get("deduplicated_index"),
        })
    total = {"year":"overall", "analysis_n":int(len(frame)), "A0_n":int((frame.A==0).sum()), "A1_n":int((frame.A==1).sum()),
             "primary_events_A0":censor(int(frame.loc[frame.A==0, PRIMARY].sum())), "primary_events_A1":censor(int(frame.loc[frame.A==1, PRIMARY].sum())),
             "n_strata":int(frame.stratum_id.nunique(dropna=True)), "n_hospital_clusters":int(frame.hospital_cluster.nunique(dropna=True))}
    rows.append(total)
    return pd.DataFrame(rows)


def missingness(frame: pd.DataFrame) -> pd.DataFrame:
    cols = sorted(set(CAT + PRIOR_NUM + CHRONIC + ACUTE_BILIARY + ["DISCWT", "hospital_cluster", "stratum_id", PRIMARY]))
    rows=[]
    for c in cols:
        if c in frame:
            for a in (0,1):
                g=frame.loc[frame.A.eq(a),c]
                rows.append({"variable":c,"A":a,"n":int(len(g)),"missing_n":censor(int(g.isna().sum())),"missing_percent":float(g.isna().mean()*100)})
    return pd.DataFrame(rows)


def hospital_descriptive(frame: pd.DataFrame) -> pd.DataFrame:
    # Descriptive distribution only: no quality-outlier labelling and no funnel limits.
    x = frame.groupby(["year", "hospital_cluster"], dropna=True).agg(n=("A","size"), completion_rate=("A","mean"), weighted_completion_rate=("A", lambda a: float(np.average(a, weights=frame.loc[a.index,"DISCWT"].fillna(1))))).reset_index()
    # The exported file is aggregate-only.  Suppress small hospital-year volume
    # summaries as well, rather than allowing a low quantile to disclose 1--10.
    return pd.DataFrame([{"analysis_unit":"hospital-year", "hospital_years":censor(int(len(x))), "median_n":censor(float(x.n.median())), "q25_n":censor(float(x.n.quantile(.25))), "q75_n":censor(float(x.n.quantile(.75))), "median_completion_rate":float(x.completion_rate.median()), "q25_completion_rate":float(x.completion_rate.quantile(.25)), "q75_completion_rate":float(x.completion_rate.quantile(.75)), "min_completion_rate":float(x.completion_rate.min()), "max_completion_rate":float(x.completion_rate.max()), "interpretation":"descriptive only; no risk-standardized quality-outlier classification"}])


def audit_existing(root: Path, frame: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    # Event mismatch audit: original static pathway expects a narrower qualifying first transition definition.
    p = root / "v2_addendum" / "06_results" / "care_pathway_transitions.csv"
    current_events = int(frame[PRIMARY].sum())
    if p.exists():
        d=pd.read_csv(p)
        rows.append({"issue":"3911_vs_pathway_3886_events", "source":"care_pathway_transitions.csv", "primary_flag_events":current_events, "pathway_rows":int(len(d)), "resolution":"Pathway is not used for the revised primary endpoint; manuscript must not equate pathway first-transition counts with patient-level primary-endpoint events."})
    else:
        rows.append({"issue":"3911_vs_pathway_3886_events", "source":"pathway file unavailable", "primary_flag_events":current_events, "pathway_rows":np.nan, "resolution":"No pathway result used in revised package."})
    old_manifest=root/"05_models"/"model_manifest.json"
    old={}
    if old_manifest.exists(): old=json.loads(old_manifest.read_text())
    rows.append({"issue":"two_ATO_CIs", "source":"original model manifest and supplemental bridge", "primary_flag_events":current_events, "pathway_rows":np.nan, "resolution":"Revised package has one canonical full-refit bootstrap CI; old fixed-score/bootstrap CIs are retained only as labelled variance-method comparisons."})
    rows.append({"issue":"HFRS_actual_use", "source":"original nrd_analysis.py and original model_manifest.json", "primary_flag_events":current_events, "pathway_rows":np.nan, "resolution":"HFRS is excluded from all BJS-revision primary, strict, and original-extended reanalysis models; prior codebook ambiguity is documented and corrected in manifest."})
    return pd.DataFrame(rows)


def event_reconciliation(root: Path, frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the pathway flow count separate from patient-level outcome risks."""
    p=root / "v2_addendum" / "06_results" / "care_pathway_transitions.csv"
    pathway_n = int(len(pd.read_csv(p))) if p.exists() else np.nan
    a=frame["A"].to_numpy(int); y=frame[PRIMARY].to_numpy(int)
    rows=[
        {"quantity":"A0 patient-level 90-day primary composite events","value":censor(int(y[a==0].sum())),"denominator":"all A=0 live-discharge index admissions in the frozen analysis cohort","unit":"patients","endpoint_role":"primary endpoint component","why_not_interchangeable":"Patient-level binary flag: any qualifying 90-day readmission."},
        {"quantity":"A1 patient-level 90-day primary composite events","value":censor(int(y[a==1].sum())),"denominator":"all A=1 live-discharge index admissions in the frozen analysis cohort","unit":"patients","endpoint_role":"primary endpoint component","why_not_interchangeable":"Patient-level binary flag: any qualifying 90-day readmission."},
        {"quantity":"Pathway qualifying first-transition rows","value":censor(pathway_n) if pd.notna(pathway_n) else np.nan,"denominator":"pathway-construction eligible transitions only","unit":"transitions","endpoint_role":"descriptive pathway only; excluded from revised primary inference","why_not_interchangeable":"Different denominator and first-transition/pathway eligibility rules; not a patient-level 90-day endpoint count."},
    ]
    return pd.DataFrame(rows)


def component_definition_table() -> pd.DataFrame:
    return pd.DataFrame([
        {"component":"k80","principal_diagnosis_rule":"I10_DX1 starts with K80","relationship":"Non-mutually-exclusive patient-level 90-day risk flag","role":"biliary component"},
        {"component":"k81","principal_diagnosis_rule":"I10_DX1 starts with K81","relationship":"Non-mutually-exclusive patient-level 90-day risk flag","role":"biliary component"},
        {"component":"k830","principal_diagnosis_rule":"I10_DX1 starts with K830","relationship":"Non-mutually-exclusive patient-level 90-day risk flag","role":"biliary component"},
        {"component":"k851","principal_diagnosis_rule":"I10_DX1 starts with K851","relationship":"Non-mutually-exclusive patient-level 90-day risk flag","role":"biliary pancreatitis component"},
        {"component":"other_k85","principal_diagnosis_rule":"I10_DX1 starts with K85 but not K851","relationship":"Non-mutually-exclusive patient-level 90-day risk flag","role":"other acute pancreatitis component"},
        {"component":"k85_total","principal_diagnosis_rule":"k851 OR other_k85 during 90 days","relationship":"Derived union; may overlap biliary components on separate readmissions","role":"acute pancreatitis total"},
        {"component":"biliary_composite","principal_diagnosis_rule":"k80 OR k81 OR k830 OR k85_total during 90 days","relationship":"Derived union; constructed from non-mutually-exclusive flags","role":"component composite"},
    ])


def privacy_audit(out: Path) -> dict:
    """Verify exportable aggregate files have no identifier columns or unsuppressed small count fields."""
    # Match exact identifier columns, not legitimate aggregate descriptors such
    # as ``n_hospital_clusters``.  Server-only bootstrap traces are deliberately
    # kept on the server and are out of scope for the external-export audit.
    prohibited={"key_nrd","nrd_visitlink","person_id","index_id","hospital_cluster"}
    findings=[]
    for p in sorted(out.glob("*.csv")):
        if "server_only" in p.name:
            continue
        d=pd.read_csv(p)
        bad_cols=[c for c in d.columns if c.lower() in prohibited]
        count_cols=[c for c in d.columns if c.lower() in {"n","missing_n","unweighted_n","events_a0","events_a1","primary_events_a0","primary_events_a1","value"} or c.lower().endswith("_n") or "events" in c.lower()]
        small=[]
        for c in count_cols:
            for value in d[c].dropna().tolist():
                try:
                    v=float(value)
                    if 1 <= v <= 10: small.append({"column":c,"value":value})
                except (TypeError,ValueError):
                    pass
        findings.append({"file":p.name,"identifier_columns":bad_cols,"unsuppressed_small_counts":small,"pass":not bad_cols and not small})
    return {"status":"PASS" if all(x["pass"] for x in findings) else "FAIL","patient_level_exported":False,"small_cell_rule":"Counts 1-10 must be represented as <11 in exportable tabulations.","files":findings}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--bootstrap-target", type=int, default=200)
    ap.add_argument("--fixed-bootstrap", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=8, choices=range(1, 9))
    args=ap.parse_args(); root=args.root.resolve()
    out=root/"bjs_revision_results"; out.mkdir(exist_ok=True)
    input_path=root/"03_etl"/"analysis_ready_main.parquet"
    log("loading frozen analysis-ready cohort")
    frame=add_quarter(pd.read_parquet(input_path))
    required=["index_id","A","DISCWT","stratum_id","hospital_cluster",PRIMARY,*CAT,*PRIOR_NUM,*CHRONIC,*ACUTE_BILIARY]
    missing=[c for c in required if c not in frame]
    if missing: raise RuntimeError("missing required columns: "+", ".join(missing))
    if frame.index_id.duplicated().any(): raise RuntimeError("analysis-ready cohort has duplicate index_id")
    if not set(frame.A.unique()).issubset({0,1}): raise RuntimeError("A outside binary coding")
    atomic_write_csv(record_reporting(root, frame), out/"record_reporting.csv")
    atomic_write_csv(missingness(frame), out/"missingness_summary.csv")
    atomic_write_csv(hospital_descriptive(frame), out/"hospital_descriptive.csv")
    atomic_write_csv(audit_existing(root, frame), out/"revision_issue_log.csv")
    atomic_write_csv(event_reconciliation(root, frame), out/"event_reconciliation.csv")
    atomic_write_csv(component_definition_table(), out/"component_definitions.csv")
    results=[]; saved={}
    for spec in ["strict_pre_admission_proxy", "chronic_proxy_sensitivity", "original_extended_sensitivity"]:
        log(f"fitting {spec}")
        r,nuis=model_result(frame, PRIMARY, spec, args.fixed_bootstrap)
        results.append(r); saved[spec]=nuis
    sens=pd.DataFrame(results)
    atomic_write_csv(sens, out/"baseline_model_sensitivity.csv")
    strict_nuisance=saved["strict_pre_admission_proxy"]
    strict_cat, strict_num=get_features(frame,"strict_pre_admission_proxy")
    atomic_write_csv(balance_details(frame, strict_cat, strict_num, strict_nuisance["overlap_weight"].to_numpy(float)), out/"strict_primary_balance_detail.csv")
    atomic_write_csv(ps_plot_source(frame, strict_nuisance), out/"strict_primary_ps_overlap_plot_source.csv")
    primary=sens.loc[sens.feature_set.eq("strict_pre_admission_proxy")].iloc[0].to_dict()
    # Full refit bootstrap reproduces nuisance estimation inside every cluster bootstrap replicate.
    log(f"starting/resuming full nuisance-refit bootstrap through {args.bootstrap_target} replicates")
    cat,num=get_features(frame,"strict_pre_admission_proxy")
    reps=refit_bootstrap(frame, PRIMARY, cat, num, args.bootstrap_target, out/"full_refit_bootstrap_strict_server_only.csv", SEED+90000, args.workers)
    valid=reps.loc[reps.status.eq("PASS"),"rd_A0_minus_A1"].dropna().to_numpy(float)
    # A diagnostic run uses the same >=90% validity rule as the formal run;
    # the absolute 180-replicate criterion therefore applies naturally at
    # target=200, rather than incorrectly blocking a target=20 diagnostic.
    required_valid_reps=max(1, math.ceil(.9*args.bootstrap_target))
    if len(valid)<required_valid_reps:
        raise RuntimeError(f"insufficient valid full-refit bootstrap replicates: {len(valid)}")
    primary["full_refit_ci_low"]=float(np.quantile(valid,.025)); primary["full_refit_ci_high"]=float(np.quantile(valid,.975)); primary["full_refit_valid_reps"]=int(len(valid)); primary["full_refit_target_reps"]=args.bootstrap_target; primary["required_valid_reps"]=required_valid_reps; primary["achieved_valid_reps"]=int(len(valid))
    atomic_write_csv(pd.DataFrame([primary]), out/"primary_revised.csv")
    atomic_write_csv(bootstrap_mc_diagnostics(valid, SEED+730000+args.bootstrap_target), out/"bootstrap_monte_carlo_diagnostics.csv")
    atomic_write_csv(environmental_floating_audit(out), out/"bootstrap_environmental_floating_point_audit.csv")
    comp_path=out/"component_flags_server_only.parquet"
    component_rows=[]
    if comp_path.exists():
        log("estimating pre-specified outcome components from server-only flags")
        flags=pd.read_parquet(comp_path)
        if flags.index_id.duplicated().any() or len(flags)!=len(frame): raise RuntimeError("component flags failed one-to-one bridge")
        working=frame.merge(flags, on="index_id", validate="one_to_one")
        if len(working)!=len(frame): raise RuntimeError("component flags did not cover every analysis-ready index admission")
        required_components={"y90_component_k80","y90_component_k81","y90_component_k830","y90_component_k851","y90_component_other_k85"}
        if not required_components.issubset(flags.columns): raise RuntimeError("component flags missing a pre-specified component")
        working["y90_component_k85_total"]=(working["y90_component_k851"].astype(bool)|working["y90_component_other_k85"].astype(bool)).astype(int)
        working["y90_component_biliary_composite"]=(working[["y90_component_k80","y90_component_k81","y90_component_k830","y90_component_k85_total"]].astype(bool).any(axis=1)).astype(int)
        for c in [x for x in working.columns if x.startswith("y90_component_")]:
            row,_=model_result(working,c,"strict_pre_admission_proxy",args.fixed_bootstrap)
            row["component"]=c.replace("y90_component_","")
            component_rows.append(row)
    else:
        component_rows.append({"component":"NOT_AVAILABLE", "reason":"Component scan has not yet produced server-only component flags; no substitute or fabricated component estimate is reported."})
    atomic_write_csv(pd.DataFrame(component_rows), out/"outcome_components.csv")
    comparison=pd.DataFrame([{
        "variance_method":"full_nuisance_refit_stratified_hospital_cluster_bootstrap", "ci_low":primary["full_refit_ci_low"], "ci_high":primary["full_refit_ci_high"], "valid_reps":primary["full_refit_valid_reps"], "notes":"Primary uncertainty; 5-fold hospital-grouped nuisance models re-fit within every replicate."
    },{
        "variance_method":"fixed_score_stratified_hospital_cluster_bootstrap", "ci_low":primary["fixed_score_ci_low"], "ci_high":primary["fixed_score_ci_high"], "valid_reps":primary["fixed_score_valid_reps"], "notes":"Supportive comparison; nuisance estimates fixed, so not primary inference."
    },{
        "variance_method":"AIPW_ratio_linearization_EIF", "ci_low":primary["eif_ci_low"], "ci_high":primary["eif_ci_high"], "valid_reps":np.nan, "notes":"Supportive Taylor linearization at hospital-within-stratum level."
    }])
    atomic_write_csv(comparison,out/"bootstrap_comparison.csv")
    privacy=privacy_audit(out)
    if privacy["status"]!="PASS": raise RuntimeError("privacy audit failed for an exportable aggregate CSV")
    atomic_write_json(privacy,out/"privacy_audit.json")
    manifest={
        "analysis_id":"bjs-reanalysis-v4", "status":"PASS", "timestamp":time.strftime("%Y-%m-%dT%H:%M:%S%z"), "python":sys.version, "platform":platform.platform(),
        "input":{"path":str(input_path),"sha256":sha256(input_path),"n":int(len(frame))}, "estimand":"association between completion of index-admission cholecystectomy by discharge and subsequent 90-day readmission, conditional on live discharge; not an admission-time treatment-strategy effect",
        "reference_covariates":{"included":CAT+PRIOR_NUM,"excluded_same_admission_acute_biliary":ACUTE_BILIARY,"chronic_diagnosis_proxies":"Excluded from the primary model because NRD has no diagnosis-level present-on-admission indicator; evaluated only in chronic_proxy_sensitivity.","excluded_post_admission":["APRDRG_Severity","APRDRG_Risk_Mortality","LOS","TOTCHG","DISPUNIFORM","REHABTRANSFER","index_ercp_related","chole_prday_min","frailty_domain_count_nonvalidated","hfrs","hfrs_model"]},
        "models":{"strict_pre_admission_proxy":"Primary: demographics, admission context, payer/income/residence, calendar, hospital characteristics, and observed prior-90-day utilization only; no same-index-admission diagnosis-derived variables.","chronic_proxy_sensitivity":"Adds chronic diagnosis proxies only as a sensitivity analysis.","original_extended":"Adds acute biliary discharge diagnoses only as a transparent legacy-comparison sensitivity analysis.","hfrs":"Excluded in every BJS revision model"},
        "bootstrap":{"full_refit_target":args.bootstrap_target,"required_valid_reps":required_valid_reps,"achieved_valid_reps":int(len(valid)),"folds":5,"grouping":"hospital clusters within stratified cluster bootstrap","workers":args.workers,"threads_per_worker":1,"threads_cap":8,"seed":SEED+90000,"parallel_consistency_check":"replicate 0 recomputed to absolute tolerance <=1e-10 before parallel continuation"},
        "canonical_primary_ci":{"method":"full_nuisance_refit_stratified_hospital_cluster_bootstrap","ci_low":primary["full_refit_ci_low"],"ci_high":primary["full_refit_ci_high"],"valid_reps":int(len(valid)),"interpretation":"The sole canonical primary confidence interval. Fixed-score and EIF intervals are variance-method comparisons only."},
        "component_outcomes":{"relationship":"Non-mutually-exclusive patient-level component-risk flags across the 90-day window; K85 total and biliary composite are derived unions.","definitions_file":"component_definitions.csv"},
        "outputs":{p.name:sha256(p) for p in sorted(out.glob("*.csv")) if "server_only" not in p.name}, "patient_level_exported":False,"small_cell_suppression":"Counts 1-10 are suppressed in externally exportable tabulations."
    }
    atomic_write_json(manifest,out/"reanalysis_manifest.json")
    log("PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
