# Reproducibility guide

## Public aggregate check

Use Python 3.11 or 3.12 with the pinned dependencies in `requirements.txt`.

```bash
python -m pip install -r requirements.txt
python figures/build_public_figures.py --output-dir build/figures
python -m unittest discover -s tests -v
python tests/validate_repository.py
```

The smoke test expects 18 non-empty figure files: six figure names multiplied by PDF, PNG, and SVG. The numeric test checks the cohort total, arm sizes, and corrected primary estimate against the public aggregate source map.

## Licensed full rerun

1. Obtain the 2018-2022 NRD Core, Severity, Hospital, and CCR files directly from HCUP.
2. Place the licensed archives in `PRIVATE_ROOT/raw/archives/{year}/` using `NRD_{year}_CORE.zip`, `NRD_{year}_Severity.zip`, `NRD_{year}_HOSPITAL.zip`, and `cc{year}NRD.zip`. Do not place them in this repository.
3. Create `PRIVATE_ROOT/00_admin/.nrd_credentials.json` from the local credential template and fill it only in the private environment. Never commit it. The orchestrator refuses any other credential location.
4. Run `run_full_pipeline.sh` on the licensed Linux server, or inside WSL on Windows, with the private root and worker count. The encrypted fixed-width reader uses a POSIX pseudo-terminal; the PowerShell wrapper is intended only for PowerShell 7 on Linux and refuses native Windows execution. The orchestrator stages the public codebook and year-specific layouts into `02_codebook/` and `00_admin/bootstrap/`, respectively, before running the licensed path.
5. The licensed sequence is: five annual cohort builds (`--year 2018` through `--year 2022`), ETL validation, the 1000-replicate primary/500-replicate secondary analysis, server-only component flags, canonical 1000-replicate full-refit bootstrap, corrected 1000-replicate bootstrap, fixed-score robustness addendum, and final aggregate manifest generation.
6. Inspect the private ETL validation report, model manifest, bootstrap diagnostics, corrected-bootstrap verification, final manifest, and privacy audit before exporting any aggregate result.
7. Apply the small-cell rule and remove record-level, linkage, hospital, nuisance-prediction, and credential artifacts before any public release.

The full rerun is expected to regenerate the 82,948-person main cohort under the frozen definitions when the same NRD release, layouts, and software versions are used. Version differences in Python packages, operating systems, and floating-point libraries can cause insignificant last-digit differences; the estimand, exclusions, seeds, bootstrap design, and output suppression policy must remain unchanged.

## Interpretation guardrails

The primary comparison begins at index discharge among survivors. It should not be relabeled as an admission-time surgical strategy effect. Index-stay outcomes such as length of stay, cost, discharge destination, and coded complications are post-treatment or treatment-period outcomes and are descriptive. ERCP-compatible ICD-10-PCS logic is a secondary structural proxy, not a validated gold standard. No patient-level output is authorized by this repository.
