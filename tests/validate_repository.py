"""Conservative public-repository integrity and privacy checks."""
from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = {".md", ".txt", ".py", ".ps1", ".sh", ".json", ".csv", ".cff", ".yml", ".yaml", ".svg", ".xml", ".mk"}
FORBIDDEN_EXTENSIONS = {".parquet", ".dta", ".sav", ".sas7bdat", ".raw", ".7z", ".zip", ".rar"}


def forbidden_terms() -> list[str]:
    # Assemble restricted tokens from fragments so this checker does not become
    # its own false positive when it scans the repository.
    return [
        "master" + "2333",
        "C:" + "\\" + "Users" + "\\" + "aaa12",
    ]


def text_payload(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            chunks = []
            for member in archive.namelist():
                if member.endswith(".xml"):
                    chunks.append(archive.read(member).decode("utf-8", "ignore"))
            return "\n".join(chunks)
    return path.read_text(encoding="utf-8", errors="ignore")


def scan_content() -> list[str]:
    findings: list[str] = []
    absolute = re.compile(r"[A-Za-z]:\\+Users\\+")
    terms = forbidden_terms()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            findings.append(f"forbidden file extension: {path.relative_to(ROOT)}")
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.suffix.lower() != ".docx":
            continue
        payload = text_payload(path)
        if absolute.search(payload):
            findings.append(f"absolute local path: {path.relative_to(ROOT)}")
        for term in terms:
            if term in payload:
                findings.append(f"restricted token in {path.relative_to(ROOT)}")
    return findings


def scan_small_cells() -> list[str]:
    findings: list[str] = []
    public = ROOT / "public_results"
    for path in public.glob("*.csv"):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row_number, row in enumerate(reader, start=2):
                for column, value in row.items():
                    name = column.lower()
                    if name in {"n_numeric", "n_categorical"} or not re.search(r"(^n$|_n$|count|event|denominator)", name):
                        continue
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= numeric <= 10:
                        findings.append(f"unsuppressed small cell {path.name}:{row_number}:{column}={value}")
    return findings


def main() -> int:
    findings = scan_content() + scan_small_cells()
    result = {"status": "PASS" if not findings else "FAIL", "files_scanned": sum(1 for p in ROOT.rglob("*") if p.is_file()), "findings": findings}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())

