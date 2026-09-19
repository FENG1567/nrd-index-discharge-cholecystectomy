from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "checksums.sha256"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def main() -> int:
    entries = []
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file() and p.name != TARGET.name and "build" not in p.parts and "__pycache__" not in p.parts):
        entries.append(f"{digest(path)}  {path.relative_to(ROOT).as_posix()}\n")
    TARGET.write_text("".join(entries), encoding="utf-8")
    print(f"checksums rows={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

