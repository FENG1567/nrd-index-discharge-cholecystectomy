# Data availability

The Healthcare Cost and Utilization Project Nationwide Readmissions Database (NRD) is a restricted, licensed third-party resource. It must be acquired directly from HCUP by an eligible investigator under the applicable data-use agreement. This repository does not include the NRD archives, extracted fixed-width records, patient-level tables, `NRD_VisitLink`, `KEY_NRD`, `HOSP_NRD`, hospital identifiers, or any other record-level linkage key.

The public package includes only de-identified aggregate summaries required for the published figures and reference tables. All counts from 1 to 10 are suppressed as `<11`; corresponding percentages are withheld where the source rule requires it. The layout JSON files describe field positions and types only and do not contain source observations.

Because the data are licensed, users must not redistribute NRD files or reconstruct patient-level records from the aggregate materials. The aggregate mode is intended for figure and numeric consistency checks, not for independent patient-level reanalysis.

