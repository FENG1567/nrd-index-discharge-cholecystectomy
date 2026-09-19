# Index-discharge cholecystectomy after coded mild biliary acute pancreatitis

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Public repository: https://github.com/FENG1567/nrd-index-discharge-cholecystectomy

This repository contains the public, aggregate-only reproducibility materials for:

> Completion of Cholecystectomy by Index Discharge and 90-Day Biliary-or-Acute-Pancreatitis Readmission After Coded Mild Biliary Acute Pancreatitis: A Nationwide NRD Study

The study uses the Healthcare Cost and Utilization Project (HCUP) Nationwide Readmissions Database (NRD), 2018-2022. NRD is licensed, restricted-access data and is not redistributed here.

## What the analysis estimates

The primary estimand is the overlap-population, NRD-design-weighted difference in 90-day inpatient readmission risk:

`risk(A=0) - risk(A=1)`

where time zero is the index discharge date among adults with principal diagnosis K85.10, eligible discharge months January-September, survival to discharge, and prespecified coded severe-disease exclusions. `A=1` means cholecystectomy was recorded during the index admission (`0FT4*`); `A=0` means no such procedure was recorded by discharge. The primary outcome is a principal-diagnosis readmission for gallstone disease, cholecystitis, cholangitis, or acute pancreatitis within 90 days. The estimand is a discharge-status association in live-discharge survivors, not an admission-time treatment-strategy effect.

The frozen primary cohort contains 82,948 index admissions (A0 34,530; A1 48,418). The corrected primary risk difference is 8.700 percentage points (95% CI 8.287-9.080), with A0 minus A1 coding. Counts from the licensed source are represented only in aggregate form and small cells are suppressed as `<11`.

## Two reproducibility modes

### Public aggregate-only mode

This mode needs no NRD files. It uses the sanitized aggregate CSV/JSON files in `public_results/` to rebuild the six reference figures.

```bash
python figures/build_public_figures.py --output-dir build/figures
python -m unittest discover -s tests -v
python tests/validate_repository.py
```

The builder writes PDF, PNG, and SVG versions of Figure 1-6 and Supplementary Figure S1-S4. The exact display artifacts supplied for the manuscript are retained under `outputs/figures/`; generated files should be written to a separate directory for comparison.

### Licensed full rerun mode

An investigator with a valid HCUP/NRD license may rerun the private pipeline. The rerun requires the licensed annual Core, Severity, Hospital, and CCR inputs and the local credentials file at exactly `PRIVATE_ROOT/00_admin/.nrd_credentials.json`. The credentials file is ignored by Git and must never be committed. The orchestrators default to eight workers and do not print credential values.

The private root should contain the licensed archives at the following paths (the archive contents and password values remain private):

```text
PRIVATE_ROOT/00_admin/.nrd_credentials.json
PRIVATE_ROOT/raw/archives/2018/NRD_2018_CORE.zip
PRIVATE_ROOT/raw/archives/2018/NRD_2018_Severity.zip
PRIVATE_ROOT/raw/archives/2018/NRD_2018_HOSPITAL.zip
PRIVATE_ROOT/raw/archives/2018/cc2018NRD.zip
...
PRIVATE_ROOT/raw/archives/2022/NRD_2022_CORE.zip
PRIVATE_ROOT/raw/archives/2022/NRD_2022_Severity.zip
PRIVATE_ROOT/raw/archives/2022/NRD_2022_HOSPITAL.zip
PRIVATE_ROOT/raw/archives/2022/cc2022NRD.zip
```

At startup the orchestrator creates the private output directories and stages `codebook/codebook_v2.0.json`, all fifteen year-specific layout files under `00_admin/bootstrap/`, `nrd_build_cohort.py` under `03_etl/`, `bjs_reanalysis.py` under `05_models/ate/`, and the robustness implementation under `bjs_revision_robustness/`. It then builds all five annual cohorts, validates the ETL, runs the 1000-replicate primary analysis and component scan, runs the canonical 1000-replicate full-refit bootstrap, the corrected 1000-replicate bootstrap, the fixed-score robustness addendum, and the final aggregate manifest. No staged or generated restricted file is copied back into this repository.

```bash
./run_full_pipeline.sh --root /path/to/private/project --workers 8
```

On Windows, run the Bash entry point inside WSL; the encrypted fixed-width reader uses a POSIX pseudo-terminal. A PowerShell 7 wrapper is also supplied for Linux hosts:

```powershell
./run_full_pipeline.ps1 -PrivateRoot /path/to/private/project -Workers 8
```

The full rerun is intentionally separate from the public aggregate mode: patient-level records, VisitLink, hospital identifiers, fitted nuisance predictions, and server-only intermediates never belong in this repository.

## Repository map

- `analysis/`: cohort construction, primary analysis, corrected bootstrap, robustness, and manifest scripts. The legacy/intermediate `nrd_analysis.py` writes the restricted analysis-ready table during a licensed run.
- `codebook/`: sanitized phenotype definitions, evidence map, ontology specification, and the 2018-2022 Core/Severity/Hospital layout specifications needed for a private rerun.
- `config/`: credential template and raw-layout template only.
- `figures/`: aggregate-only figure builder.
- `public_results/`: aggregate inputs used by the figure builder and data dictionary.
- `outputs/figures/`: exact manuscript display artifacts; `outputs/tables/`: reference tables and figure legends.
- `tests/`: codebook, privacy/integrity, numeric-consistency, and figure smoke tests.

## Important limitations

The NRD does not provide complete outpatient follow-up, diagnosis-level present-on-admission indicators, or a validated ERCP gold standard. The primary model therefore uses prespecified demographics, admission context, payer/income/residence, calendar, hospital characteristics, and prior observed 90-day utilisation; chronic diagnosis proxies are reserved for sensitivity analysis. Acute-prone discharge-code features stay out of confirmatory adjustment, and ERCP-related procedure logic is secondary. Index-stay length of stay, charges/costs, discharge destination, severity fields, and post-treatment complications are not baseline confounders. Resource and inpatient-harm summaries are descriptive and should not be interpreted as the causal counterpart of the 90-day estimand.

The calendar-year linkage rule is strict: `NRD_VisitLink` and `HOSP_NRD` are never linked across years. The January-September index restriction is required for a natural 90-day within-year observation window. The public package cannot reproduce patient-level ETL without a licensed NRD environment.

## Data and code policy

HCUP terms govern acquisition, use, and redistribution of NRD. This repository redistributes code, layout metadata, phenotype definitions, and aggregate summaries only. See `DATA_AVAILABILITY.md`, `CODE_AVAILABILITY.md`, and `REPRODUCIBILITY.md` before reuse.
