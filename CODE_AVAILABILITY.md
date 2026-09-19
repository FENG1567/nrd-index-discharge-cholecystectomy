# Code availability

Repository: https://github.com/FENG1567/nrd-index-discharge-cholecystectomy

The repository is public and is released under the MIT License for the original
code. The NRD data are not included and cannot be redistributed. Access to the
2018–2022 NRD files requires an independent HCUP data-use agreement.

The repository contains the cohort-builder, analysis, corrected bootstrap, robustness, manifest, figure-builder, and validation scripts used in the study. Code is provided for transparency and reproducibility; it does not grant access to HCUP data.

The public aggregate pathway is self-contained:

```text
public_results/ -> figures/build_public_figures.py -> PDF/PNG/SVG figures
```

The licensed pathway is deliberately fail-closed. It requires a private project root with the licensed archives under `raw/archives/{year}/` and the local ignored credentials file at `PRIVATE_ROOT/00_admin/.nrd_credentials.json` before it will start. The orchestrator stages the sanitized codebook/layout metadata and the two private-import helper scripts, then runs the five annual ETL builds, primary analysis, component scan, canonical and corrected bootstrap paths, robustness addendum, and final manifest. Patient-level and server-only intermediate artifacts are written only to the private root. Default parallelism is eight workers and credential values are never printed.

The scripts preserve the prespecified cohort definitions, treatment timing, baseline-variable blacklist, overlap estimand, hospital-grouped cross-fitting, corrected bootstrap, and small-cell protections. The full patient-level path cannot be tested in this public environment without the licensed NRD files.

MIT licensing applies to original repository code; HCUP data and HCUP documentation remain subject to their own terms.

## Reuse and citation

For a public smoke test, run the aggregate-only figure builder and validation
commands in the README. For a licensed rerun, use only locally held NRD files
and the private configuration template. Do not commit credentials, archive
passwords, patient-level extracts, hospital identifiers, fitted nuisance
predictions, or server-only intermediates. Please cite the associated paper and
this repository's `CITATION.cff` record when reusing the code or aggregate
results.
