#!/usr/bin/env bash
set -euo pipefail

PRIVATE_ROOT=""
WORKERS="8"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SEVEN_ZIP_BIN="${SEVEN_ZIP_BIN:-7z}"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) PRIVATE_ROOT="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --seven-zip) SEVEN_ZIP_BIN="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PRIVATE_ROOT" || ! -d "$PRIVATE_ROOT" ]]; then
  echo "A private licensed project root is required; aggregate-only users should run the figure builder." >&2
  exit 2
fi
if ! [[ "$WORKERS" =~ ^[1-8]$ ]]; then
  echo "--workers must be an integer from 1 to 8." >&2
  exit 2
fi

ADMIN_DIR="$PRIVATE_ROOT/00_admin"
CREDENTIALS="$ADMIN_DIR/.nrd_credentials.json"
if [[ ! -f "$CREDENTIALS" ]]; then
  echo "Missing PRIVATE_ROOT/00_admin/.nrd_credentials.json; refusing to start." >&2
  exit 2
fi

stage_file() {
  local source="$1" destination="$2"
  mkdir -p "$(dirname -- "$destination")"
  if [[ -e "$destination" ]]; then
    if ! cmp -s -- "$source" "$destination"; then
      echo "Existing staged metadata differs: $destination" >&2
      exit 2
    fi
  else
    cp -- "$source" "$destination"
  fi
}

mkdir -p "$ADMIN_DIR/bootstrap" "$PRIVATE_ROOT/02_codebook" "$PRIVATE_ROOT/03_etl" \
  "$PRIVATE_ROOT/04_qc" "$PRIVATE_ROOT/05_models/ate" "$PRIVATE_ROOT/06_results" \
  "$PRIVATE_ROOT/bjs_revision_results" "$PRIVATE_ROOT/bjs_corrected_bootstrap" \
  "$PRIVATE_ROOT/bjs_revision_robustness" "$PRIVATE_ROOT/public_export"

stage_file "$REPO_ROOT/codebook/codebook_v2.0.json" "$PRIVATE_ROOT/02_codebook/codebook_v2.0.json"
for layout in "$REPO_ROOT"/codebook/layouts/*.json; do
  stage_file "$layout" "$ADMIN_DIR/bootstrap/$(basename -- "$layout")"
done
stage_file "$REPO_ROOT/analysis/nrd_build_cohort.py" "$PRIVATE_ROOT/03_etl/nrd_build_cohort.py"
stage_file "$REPO_ROOT/analysis/bjs_reanalysis.py" "$PRIVATE_ROOT/05_models/ate/bjs_reanalysis.py"
stage_file "$REPO_ROOT/analysis/robustness_addendum.py" "$PRIVATE_ROOT/bjs_revision_robustness/robustness_addendum.py"

for year in 2018 2019 2020 2021 2022; do
  archive_dir="$PRIVATE_ROOT/raw/archives/$year"
  for file in "NRD_${year}_CORE.zip" "NRD_${year}_Severity.zip" "NRD_${year}_HOSPITAL.zip" "cc${year}NRD.zip"; do
    if [[ ! -f "$archive_dir/$file" ]]; then
      echo "Missing licensed archive: $archive_dir/$file" >&2
      exit 2
    fi
  done
  "$PYTHON_BIN" "$REPO_ROOT/analysis/nrd_build_cohort.py" \
    --root "$PRIVATE_ROOT" --year "$year" --seven-zip "$SEVEN_ZIP_BIN"
done

"$PYTHON_BIN" "$REPO_ROOT/analysis/validate_etl.py" --root "$PRIVATE_ROOT"
"$PYTHON_BIN" "$REPO_ROOT/analysis/nrd_analysis.py" --root "$PRIVATE_ROOT" --bootstrap 1000 --secondary-bootstrap 500
"$PYTHON_BIN" "$REPO_ROOT/analysis/build_component_flags.py" --root "$PRIVATE_ROOT" --seven-zip "$SEVEN_ZIP_BIN"
"$PYTHON_BIN" "$REPO_ROOT/analysis/bjs_reanalysis.py" --root "$PRIVATE_ROOT" --bootstrap-target 1000 --fixed-bootstrap 1000 --workers "$WORKERS"
"$PYTHON_BIN" "$REPO_ROOT/analysis/corrected_bootstrap.py" \
  --source "$REPO_ROOT/analysis/bjs_reanalysis.py" \
  --input "$PRIVATE_ROOT/03_etl/analysis_ready_main.parquet" \
  --old-output "$PRIVATE_ROOT/bjs_revision_results" \
  --out "$PRIVATE_ROOT/bjs_corrected_bootstrap" --target 1000
"$PYTHON_BIN" "$REPO_ROOT/analysis/robustness_addendum.py" --root "$PRIVATE_ROOT" --fixed-bootstrap 1000
"$PYTHON_BIN" "$REPO_ROOT/analysis/prepare_final_manifest.py" \
  --canonical-dir "$PRIVATE_ROOT/bjs_revision_results" \
  --corrected-dir "$PRIVATE_ROOT/bjs_corrected_bootstrap" \
  --addendum-dir "$PRIVATE_ROOT/bjs_revision_robustness" \
  --output "$PRIVATE_ROOT/public_export/analysis_manifest.json"

echo "Licensed rerun completed. Review private privacy audits before releasing aggregate files."
