# NRD mild biliary acute pancreatitis clinical coding ontology

Version 1.0.1, amended before outcome modeling on 2026-09-11.

## Primary estimand

The target population is adults with principal diagnosis `K85.10`, no prespecified severe-disease proxy, valid within-year linkage and timing, discharge month January through September, and survival to index discharge. The exposure is cholecystectomy completed during the index admission (`0FT4*`) versus not completed by discharge. Time zero is the index discharge date. The primary estimand is the 90-day absolute risk difference `risk(A=0) - risk(A=1)` in the overlap population represented by the NRD sampling frame.

## Outcome hierarchy

The primary outcome is a readmission whose principal diagnosis indicates gallstone disease (`K80*`), cholecystitis (`K81*`), cholangitis (`K83.0*`), or acute pancreatitis (`K85*`). This principal-diagnosis definition is frozen before adjusted outcome modeling because it is more specific for the reason for readmission than the provisional Stage 1 any-diagnosis composite.

The narrow sensitivity outcome replaces all acute pancreatitis with biliary acute pancreatitis (`K85.1*`). The broad sensitivity outcome reproduces the Stage 1 bridge definition using any diagnosis in `K80*` through `K85*`, including `K82*` and `K83*`. All-cause readmission and principal-diagnosis `K85*` and `K85.1*` outcomes are secondary.

## Severity exclusion

The main cohort excludes shock (`R57*`), acute or acute-on-chronic respiratory failure (`J96.0*` or `J96.2*`), severe sepsis (`R65.20`, `R65.21`), invasive mechanical ventilation (`5A1935Z`, `5A1945Z`, `5A1955Z`), and renal replacement procedures (`5A1D*`). Chronic respiratory failure (`J96.1*`) is retained as a chronic condition rather than misclassified as acute organ failure. `APRDRG_Severity` is not used to define the main cohort.

## Treatment and procedure timing

Any `0FT4*` procedure establishes the discharge-status exposure. Its matched `PRDAYn` is audited. A missing or invalid procedure day does not change the primary exposure but excludes that record from procedure-timing analyses. ERCP-related procedures use a structural ICD-10-PCS algorithm restricted to hepatobiliary or pancreatic duct body parts, an endoscopic natural-orifice approach, and compatible root operations. This ERCP definition is secondary and is never presented as a gold standard.

## Baseline variables

Demographic, access, admission-path, chronic comorbidity, biliary phenotype, prior-utilization, hospital, and calendar variables are eligible baseline domains. Chronic comorbidities are reconstructed from `I10_DX2-I10_DX40` with the frozen Quan mappings in `comorbidipy` 0.5.0. Because NRD does not provide diagnosis-level present-on-admission indicators, the primary propensity model uses only prespecified chronic Elixhauser components that are unlikely to result from the index operation. Acute-prone discharge-code components (coagulopathy, fluid/electrolyte disorders, blood-loss or deficiency anaemia, and weight loss) are excluded from the primary model. The Hospital Frailty Risk Score is computed only at age 75 years or older but is moved to a sensitivity model rather than treated as a temporally secure baseline covariate. A separate six-domain frailty count remains explicitly nonvalidated and is not used for confirmatory adjustment.

`APRDRG_Severity`, `APRDRG_Risk_Mortality`, `LOS`, `TOTCHG`, discharge disposition, index-stay complications, and procedures occurring after treatment are treatment-period variables and are forbidden from the propensity model.

## Annual validity and normalization

Codes are uppercased and stripped of decimal points. The selected diagnosis and procedure families are valid during 2018-2022; the cholangitis prefix `K830` accommodates the pre-split and post-split codes. Annual file layouts are read only from the locked HCUP specifications. `NRD_VisitLink` and `HOSP_NRD` are never linked across years.

## Review record

The codebook was reviewed and agreed by the authors for clinical meaning, time ordering, source field, role, and the distinction between confirmatory and sensitivity analyses. The primary readmission endpoint was narrowed from the Stage 1 any-diagnosis composite to a principal-diagnosis composite, chronic respiratory failure was removed from the severe exclusion, the ERCP algorithm was quarantined as secondary, and the nonvalidated frailty proxy was prohibited from confirmatory inference. A pre-outcome-model amendment moved HFRS and acute-prone discharge-code comorbidity measures out of the primary propensity model because diagnosis-level POA is unavailable. This amendment was made after ETL but before any adjusted treatment-effect estimate was produced; it does not change cohort membership, exposure, or outcomes. Remaining limitations are documented rather than resolved by data-driven code changes.
