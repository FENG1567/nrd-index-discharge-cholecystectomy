from __future__ import annotations

import csv
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"FILE_MANIFEST.csv", "checksums.sha256"}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def category(relative: str) -> str:
    if relative.startswith("analysis/") or relative.startswith("figures/"):
        return "code"
    if relative.startswith("tests/") or relative.startswith(".github/"):
        return "validation"
    if relative.startswith("public_results/"):
        return "public_aggregate"
    if relative.startswith("outputs/figures/"):
        return "reference_figure"
    if relative.startswith("outputs/tables/"):
        return "reference_table"
    if relative.startswith("codebook/layouts/"):
        return "layout_metadata"
    if relative.startswith("codebook/"):
        return "codebook"
    if relative.startswith("config/"):
        return "configuration_template"
    if relative.startswith("environment/"):
        return "environment"
    return "documentation"


def main() -> int:
    rows = []
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file() and not any(part in {".git", "build", "__pycache__"} for part in p.parts) and p.name not in EXCLUDED):
        relative = path.relative_to(ROOT).as_posix()
        rows.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": digest(path), "category": category(relative), "provenance_source_path_class": "public-source-or-repository-derived", "public_status": "public"})
    target = ROOT / "FILE_MANIFEST.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"manifest rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
