[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PrivateRoot,
    [ValidateRange(1, 8)]
    [int]$Workers = 8,
    [string]$Python = "python",
    [string]$SevenZip = "7z"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:OS -eq "Windows_NT") {
    throw "The encrypted fixed-width reader uses a POSIX pseudo-terminal. Run run_full_pipeline.sh in WSL or on the licensed Linux server."
}
if (-not (Test-Path -LiteralPath $PrivateRoot -PathType Container)) {
    throw "A private licensed project root is required."
}
$PrivateRoot = (Resolve-Path -LiteralPath $PrivateRoot).Path
$AdminDir = Join-Path $PrivateRoot "00_admin"
$CredentialPath = Join-Path $AdminDir ".nrd_credentials.json"
if (-not (Test-Path -LiteralPath $CredentialPath -PathType Leaf)) {
    throw "Missing PRIVATE_ROOT\00_admin\.nrd_credentials.json; refusing to start."
}

function Stage-File {
    param([string]$Source, [string]$Destination)
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
        $destinationHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($sourceHash -ne $destinationHash) {
            throw "Existing staged metadata differs: $Destination"
        }
    } else {
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}

@(
    "00_admin\bootstrap", "02_codebook", "03_etl", "04_qc", "05_models\ate",
    "06_results", "bjs_revision_results", "bjs_corrected_bootstrap",
    "bjs_revision_robustness", "public_export"
) | ForEach-Object { New-Item -ItemType Directory -Path (Join-Path $PrivateRoot $_) -Force | Out-Null }

Stage-File (Join-Path $RepoRoot "codebook\codebook_v2.0.json") (Join-Path $PrivateRoot "02_codebook\codebook_v2.0.json")
Get-ChildItem -LiteralPath (Join-Path $RepoRoot "codebook\layouts") -Filter "*.json" -File | ForEach-Object {
    Stage-File $_.FullName (Join-Path $PrivateRoot ("00_admin\bootstrap\" + $_.Name))
}
Stage-File (Join-Path $RepoRoot "analysis\nrd_build_cohort.py") (Join-Path $PrivateRoot "03_etl\nrd_build_cohort.py")
Stage-File (Join-Path $RepoRoot "analysis\bjs_reanalysis.py") (Join-Path $PrivateRoot "05_models\ate\bjs_reanalysis.py")
Stage-File (Join-Path $RepoRoot "analysis\robustness_addendum.py") (Join-Path $PrivateRoot "bjs_revision_robustness\robustness_addendum.py")

foreach ($year in 2018..2022) {
    $archiveDir = Join-Path $PrivateRoot ("raw\archives\" + $year)
    foreach ($name in @("NRD_${year}_CORE.zip", "NRD_${year}_Severity.zip", "NRD_${year}_HOSPITAL.zip", "cc${year}NRD.zip")) {
        $archive = Join-Path $archiveDir $name
        if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
            throw "Missing licensed archive: $archive"
        }
    }
    & $Python (Join-Path $RepoRoot "analysis\nrd_build_cohort.py") --root $PrivateRoot --year $year --seven-zip $SevenZip
}

& $Python (Join-Path $RepoRoot "analysis\validate_etl.py") --root $PrivateRoot
& $Python (Join-Path $RepoRoot "analysis\nrd_analysis.py") --root $PrivateRoot --bootstrap 1000 --secondary-bootstrap 500
& $Python (Join-Path $RepoRoot "analysis\build_component_flags.py") --root $PrivateRoot --seven-zip $SevenZip
& $Python (Join-Path $RepoRoot "analysis\bjs_reanalysis.py") --root $PrivateRoot --bootstrap-target 1000 --fixed-bootstrap 1000 --workers $Workers
& $Python (Join-Path $RepoRoot "analysis\corrected_bootstrap.py") --source (Join-Path $RepoRoot "analysis\bjs_reanalysis.py") --input (Join-Path $PrivateRoot "03_etl\analysis_ready_main.parquet") --old-output (Join-Path $PrivateRoot "bjs_revision_results") --out (Join-Path $PrivateRoot "bjs_corrected_bootstrap") --target 1000
& $Python (Join-Path $RepoRoot "analysis\robustness_addendum.py") --root $PrivateRoot --fixed-bootstrap 1000
& $Python (Join-Path $RepoRoot "analysis\prepare_final_manifest.py") --canonical-dir (Join-Path $PrivateRoot "bjs_revision_results") --corrected-dir (Join-Path $PrivateRoot "bjs_corrected_bootstrap") --addendum-dir (Join-Path $PrivateRoot "bjs_revision_robustness") --output (Join-Path $PrivateRoot "public_export\analysis_manifest.json")

Write-Host "Licensed rerun completed. Review private privacy audits before releasing aggregate files."
