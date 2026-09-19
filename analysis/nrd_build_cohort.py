#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


EXPECTED_CORE = {
    2018: 17686511,
    2019: 18132856,
    2020: 16692694,
    2021: 16805508,
    2022: 16517253,
}
WINDOWS = (30, 60, 90)


class PipelineError(RuntimeError):
    pass


def nstr(value: object) -> str:
    return "" if value is None else str(value).strip().upper().replace(".", "")


def as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def starts_any(code: object, prefixes: Iterable[str]) -> bool:
    value = nstr(code)
    return bool(value) and any(value.startswith(prefix) for prefix in prefixes)


@dataclass(frozen=True)
class Layout:
    fields: tuple[tuple[str, int, int, str], ...]
    width: int
    lookup: dict[str, tuple[int, int, int, str]]

    @classmethod
    def from_spec(cls, path: Path) -> "Layout":
        payload = json.loads(path.read_text(encoding="utf-8"))
        columns = payload["specification"]["ordered_columns"]
        offset = 0
        fields = []
        for column in columns:
            length = int(column["length"])
            fields.append((column["name"], offset, offset + length, column["type"]))
            offset += length
        frozen = tuple(fields)
        return cls(frozen, offset, {name: (i, start, end, typ) for i, (name, start, end, typ) in enumerate(frozen)})

    def parse(self, raw: bytes) -> "ProjectedRow":
        return ProjectedRow(self, raw)


class ProjectedRow:
    def __init__(self, layout: Layout, raw: bytes):
        line = raw.rstrip(b"\r\n")
        if b"," in line:
            self.values = line.split(b",")
            if len(self.values) != len(layout.fields):
                raise PipelineError(f"row has {len(self.values)} fields; expected {len(layout.fields)}")
            self.fixed = None
        else:
            if len(line) != layout.width:
                raise PipelineError(f"fixed-width row has {len(line)} bytes; expected {layout.width}")
            self.values, self.fixed = None, line
        self.layout = layout
        self.cache: dict[str, object] = {}

    def get(self, name: str, default: object = None) -> object:
        if name not in self.layout.lookup:
            return default
        if name in self.cache:
            return self.cache[name]
        index, start, end, typ = self.layout.lookup[name]
        raw = self.values[index] if self.values is not None else self.fixed[start:end]  # type: ignore[index]
        value = raw.strip().decode("ascii", "strict")
        if not value:
            result: object = None
        elif typ.lower() == "num":
            try:
                result = float(value) if "." in value else int(value)
            except ValueError as exc:
                raise PipelineError(f"invalid numeric value in {name}") from exc
        else:
            result = value.upper()
        self.cache[name] = result
        return result


class SevenZipLines:
    def __init__(self, executable: str, archive: Path, password: str):
        self.executable = executable
        self.archive = archive
        self.password = password
        self.proc: subprocess.Popen[bytes] | None = None
        self.pty_master: int | None = None
        self.pty_drain: threading.Thread | None = None
        self.pty_messages = bytearray()

    def _member(self) -> str:
        completed = subprocess.run(
            [self.executable, "l", "-slt", str(self.archive)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise PipelineError("7-Zip could not list archive")
        members = []
        for line in completed.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Path = "):
                item = line[7:].strip()
                if item and not item.casefold().endswith(".zip"):
                    members.append(item)
        if len(members) != 1:
            raise PipelineError(f"expected one data member, found {len(members)}")
        return members[0]

    def __enter__(self) -> BinaryIO:
        if os.name != "posix":
            raise PipelineError("credential-safe encrypted extraction requires a POSIX pseudo-terminal")
        import fcntl
        import pty
        import termios

        member = self._member()
        master, slave = pty.openpty()
        self.pty_master = master

        def child_terminal() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(
            [self.executable, "e", "-so", str(self.archive), member],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=slave,
            close_fds=True,
            preexec_fn=child_terminal,
        )
        os.close(slave)
        password_prompt = threading.Event()

        def drain_terminal() -> None:
            try:
                while chunk := os.read(master, 4096):
                    if len(self.pty_messages) < 65536:
                        self.pty_messages.extend(chunk[: 65536 - len(self.pty_messages)])
                    if b"password" in chunk.lower():
                        password_prompt.set()
            except OSError:
                pass

        self.pty_drain = threading.Thread(target=drain_terminal, daemon=True)
        self.pty_drain.start()
        password_prompt.wait(timeout=0.5)
        os.write(master, (self.password + "\n").encode("utf-8"))
        assert self.proc.stdout is not None
        return self.proc.stdout

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.proc is not None
        if self.proc.stdout:
            self.proc.stdout.close()
        exit_code = self.proc.wait()
        if self.pty_master is not None:
            os.close(self.pty_master)
        if self.pty_drain is not None:
            self.pty_drain.join(timeout=2)
        if exc_type is None and exit_code != 0:
            message = self.pty_messages.decode("utf-8", "replace").replace(self.password, "<redacted>")
            raise PipelineError("7-Zip extraction or CRC validation failed: " + message[-2000:])


def iter_rows(layout: Layout, source: BinaryIO, progress_every: int, label: str):
    for number, raw in enumerate(source, 1):
        if progress_every and number % progress_every == 0:
            print(f"{label}: {number:,}", file=sys.stderr, flush=True)
        yield number, layout.parse(raw)


@dataclass
class Candidate:
    key: str
    visit: str
    nde: int
    los: int
    fields: dict[str, object]
    dx: tuple[str, ...]
    pr: tuple[str, ...]
    prday: tuple[int | None, ...]
    A: int
    outcomes: dict[str, int] = field(default_factory=dict)
    prior_admissions_90d: int = 0
    prior_biliary_90d: int = 0
    prior_pancreatitis_90d: int = 0
    same_day_records: int = 0
    overlapping_records: int = 0

    @property
    def discharge_day(self) -> int:
        return self.nde + self.los


def severe_hit(dx: tuple[str, ...], pr: tuple[str, ...], cb: dict) -> tuple[bool, tuple[str, ...]]:
    rules = cb["cohort"]["severe_exclusions"]
    hits = []
    if any(starts_any(x, rules["shock_any_dx_prefix"]) for x in dx):
        hits.append("shock")
    if any(starts_any(x, rules["acute_or_acute_on_chronic_respiratory_failure_any_dx_prefix"]) for x in dx):
        hits.append("acute_respiratory_failure")
    if any(x in rules["severe_sepsis_any_dx_exact"] for x in dx):
        hits.append("severe_sepsis")
    if any(x in rules["invasive_mechanical_ventilation_any_pr_exact"] for x in pr):
        hits.append("invasive_mechanical_ventilation")
    if any(starts_any(x, rules["renal_replacement_any_pr_prefix"]) for x in pr):
        hits.append("renal_replacement")
    return bool(hits), tuple(hits)


def ercp_related(code: str, cb: dict) -> bool:
    cfg = cb["ercp_related_inpatient_procedure"]["pcs_logic"]
    return (
        len(code) == 7
        and code.startswith(cfg["section_body_system_prefix"])
        and code[2] in cfg["root_operations"]
        and code[3] in cfg["body_parts"]
        and code[4] == cfg["approach_character"]
    )


def make_candidate(row: ProjectedRow, year: int, cb: dict) -> Candidate | None:
    key, visit = nstr(row.get("KEY_NRD")), nstr(row.get("NRD_VisitLink"))
    nde, los = as_int(row.get("NRD_DaysToEvent")), as_int(row.get("LOS"))
    if not key or not visit or nde is None or nde < 0 or los is None or los < 0:
        return None
    dx = tuple(nstr(row.get(f"I10_DX{i}")) for i in range(1, 41))
    pr = tuple(nstr(row.get(f"I10_PR{i}")) for i in range(1, 26))
    prday = tuple(as_int(row.get(f"PRDAY{i}")) for i in range(1, 26))
    A = int(any(starts_any(code, cb["exposure"]["cholecystectomy_any_pr_prefix"]) for code in pr))
    names = [
        "AGE", "FEMALE", "PAY1", "ZIPINC_QRTL", "PL_NCHS", "RESIDENT",
        "HCUP_ED", "ELECTIVE", "AWEEKEND", "HOSP_NRD", "NRD_STRATUM",
        "DISCWT", "DMONTH", "DIED", "DISPUNIFORM", "REHABTRANSFER", "TOTCHG",
    ]
    return Candidate(key, visit, nde, los, {name: row.get(name) for name in names}, dx, pr, prday, A)


def keep_earliest(store: dict[str, Candidate], candidate: Candidate, qc: Counter) -> None:
    old = store.get(candidate.visit)
    if old is None or (candidate.nde, candidate.key) < (old.nde, old.key):
        store[candidate.visit] = candidate
    if old is not None and candidate.nde == old.nde:
        qc["same_day_eligible_tie"] += 1


def index_pass(
    archive: Path,
    layout: Layout,
    password: str,
    seven_zip: str,
    year: int,
    cb: dict,
    progress_every: int,
) -> tuple[dict[str, dict[str, Candidate]], dict]:
    stores: dict[str, dict[str, Candidate]] = {"main": {}, "broad": {}, "harm": {}}
    flows = {name: Counter() for name in stores}
    qc = Counter()
    with SevenZipLines(seven_zip, archive, password) as source:
        for number, row in iter_rows(layout, source, progress_every, f"{year} core pass1"):
            for flow in flows.values():
                flow["raw_records"] += 1
            age = as_int(row.get("AGE"))
            if age is None or age < 18:
                continue
            for flow in flows.values():
                flow["adult"] += 1
            dx1 = nstr(row.get("I10_DX1"))
            is_main = dx1 in cb["cohort"]["main_principal_dx_exact"]
            is_broad = starts_any(dx1, cb["cohort"]["broad_principal_dx_prefix"])
            if not is_broad:
                continue
            flows["broad"]["diagnosis_match"] += 1
            if is_main:
                flows["main"]["diagnosis_match"] += 1
                flows["harm"]["diagnosis_match"] += 1
            dx = tuple(nstr(row.get(f"I10_DX{i}")) for i in range(1, 41))
            pr = tuple(nstr(row.get(f"I10_PR{i}")) for i in range(1, 26))
            severe, reasons = severe_hit(dx, pr, cb)
            if severe:
                for reason in reasons:
                    qc[f"severe_{reason}"] += 1
                continue
            flows["broad"]["no_severe_proxy"] += 1
            if is_main:
                flows["main"]["no_severe_proxy"] += 1
                flows["harm"]["no_severe_proxy"] += 1
            candidate = make_candidate(row, year, cb)
            if candidate is None:
                qc["invalid_key_link_or_time"] += 1
                continue
            if is_main:
                keep_earliest(stores["harm"], candidate, qc)
                flows["harm"]["valid_candidate"] += 1
            died = as_int(row.get("DIED"))
            if died != cb["cohort"]["survived_discharge_value"]:
                continue
            if is_main:
                flows["main"]["survived_discharge"] += 1
            flows["broad"]["survived_discharge"] += 1
            if as_int(row.get("DMONTH")) not in cb["cohort"]["discharge_months"]:
                continue
            if is_main:
                flows["main"]["discharge_month_1_to_9"] += 1
                keep_earliest(stores["main"], candidate, qc)
                flows["main"]["valid_candidate"] += 1
            flows["broad"]["discharge_month_1_to_9"] += 1
            keep_earliest(stores["broad"], candidate, qc)
            flows["broad"]["valid_candidate"] += 1
        if number != EXPECTED_CORE[year]:
            raise PipelineError(f"Core row count {number} differs from official {EXPECTED_CORE[year]}")
    for name, store in stores.items():
        flows[name]["deduplicated_index"] = len(store)
    return stores, {"flows": {k: dict(v) for k, v in flows.items()}, "pass1_qc": dict(qc)}


def outcome_flags(dx: tuple[str, ...], pr: tuple[str, ...], cb: dict) -> dict[str, bool]:
    dx1 = dx[0]
    out = cb["outcomes"]
    return {
        "all_cause": True,
        "primary_middle": starts_any(dx1, out["primary_middle_principal"]["principal_dx_prefix"]),
        "narrow_principal": starts_any(dx1, out["narrow_principal"]["principal_dx_prefix"]),
        "wide_any_dx": any(starts_any(x, out["wide_any_dx"]["any_dx_prefix"]) for x in dx),
        "principal_k85": starts_any(dx1, out["recurrent_acute_pancreatitis"]["principal_dx_prefix"]),
        "principal_k851": starts_any(dx1, out["recurrent_biliary_acute_pancreatitis"]["principal_dx_prefix"]),
        "subsequent_cholecystectomy": any(starts_any(x, out["subsequent_inpatient_cholecystectomy"]["any_pr_prefix"]) for x in pr),
        "subsequent_ercp_related": any(ercp_related(x, cb) for x in pr if x),
    }


def timeline_pass(
    archive: Path,
    layout: Layout,
    password: str,
    seven_zip: str,
    year: int,
    cb: dict,
    stores: dict[str, dict[str, Candidate]],
    progress_every: int,
) -> dict:
    by_visit: dict[str, list[tuple[str, Candidate]]] = {}
    for cohort, store in stores.items():
        for visit, candidate in store.items():
            by_visit.setdefault(visit, []).append((cohort, candidate))
    qc = Counter()
    with SevenZipLines(seven_zip, archive, password) as source:
        for number, row in iter_rows(layout, source, progress_every, f"{year} core pass2"):
            visit = nstr(row.get("NRD_VisitLink"))
            linked = by_visit.get(visit)
            if not linked:
                continue
            key = nstr(row.get("KEY_NRD"))
            nde, los = as_int(row.get("NRD_DaysToEvent")), as_int(row.get("LOS"))
            if nde is None or los is None or los < 0:
                qc["linked_record_invalid_time"] += 1
                continue
            dx = tuple(nstr(row.get(f"I10_DX{i}")) for i in range(1, 41))
            pr = tuple(nstr(row.get(f"I10_PR{i}")) for i in range(1, 26))
            flags = outcome_flags(dx, pr, cb)
            for cohort, candidate in linked:
                if key == candidate.key:
                    continue
                previous_gap = candidate.nde - (nde + los)
                if nde < candidate.nde and 0 <= previous_gap <= 90:
                    candidate.prior_admissions_90d += 1
                    if any(starts_any(x, cb["outcomes"]["wide_any_dx"]["any_dx_prefix"]) for x in dx):
                        candidate.prior_biliary_90d = 1
                    if starts_any(dx[0], ["K85"]):
                        candidate.prior_pancreatitis_90d = 1
                gap = nde - candidate.discharge_day
                if gap < 0:
                    if nde >= candidate.nde:
                        candidate.overlapping_records += 1
                    continue
                if gap == 0:
                    candidate.same_day_records += 1
                if gap > 90:
                    continue
                for window in WINDOWS:
                    if gap <= window:
                        for outcome, hit in flags.items():
                            if hit:
                                candidate.outcomes[f"y{window}_{outcome}_gap0"] = 1
                                if gap >= 1:
                                    candidate.outcomes[f"y{window}_{outcome}"] = 1
        if number != EXPECTED_CORE[year]:
            raise PipelineError(f"Core pass2 row count {number} differs from official {EXPECTED_CORE[year]}")
    return dict(qc)


def load_lookup(
    archive: Path,
    layout: Layout,
    password: str,
    seven_zip: str,
    key_name: str,
    wanted: set[str] | None,
    expected_rows: int | None,
) -> tuple[dict[str, dict[str, object]], int]:
    records: dict[str, dict[str, object]] = {}
    with SevenZipLines(seven_zip, archive, password) as source:
        for number, row in iter_rows(layout, source, 0, archive.name):
            key = nstr(row.get(key_name))
            if key and (wanted is None or key in wanted):
                records[key] = {name: row.get(name) for name, *_ in layout.fields}
        if expected_rows is not None and number != expected_rows:
            raise PipelineError(f"{archive.name} row count {number} differs from official {expected_rows}")
    return records, number


def load_ccr(archive: Path, password: str, seven_zip: str) -> dict[str, dict[str, object]]:
    result = {}
    with SevenZipLines(seven_zip, archive, password) as source:
        reader = csv.DictReader((line.decode("utf-8", "replace") for line in source), quotechar="'")
        required = {"HOSP_NRD", "YEAR", "CCR_NRD", "WAGEINDEX"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise PipelineError("CCR fields missing")
        for row in reader:
            key = nstr(row.get("HOSP_NRD"))
            if key:
                result[key] = row
    return result


def archive_paths(root: Path, year: int) -> dict[str, Path]:
    folder = root / "raw" / "archives" / str(year)
    files = list(folder.glob("*.zip"))
    wanted = {
        "core": f"NRD_{year}_CORE.zip",
        "severity": f"NRD_{year}_Severity.zip",
        "hospital": f"NRD_{year}_HOSPITAL.zip",
        "ccr": f"cc{year}NRD.zip",
    }
    out = {}
    for kind, name in wanted.items():
        match = next((x for x in files if x.name.casefold() == name.casefold()), None)
        if match is None:
            raise FileNotFoundError(name)
        out[kind] = match
    return out


def hashed_id(year: int, value: str, label: str) -> str:
    return hashlib.sha256(f"{label}|{year}|{value}".encode("utf-8")).hexdigest()[:24]


def fraud_proxy(dx: tuple[str, ...], cb: dict) -> tuple[int, int]:
    count = 0
    for prefixes in cb["frailty_proxy"]["domains"].values():
        count += int(any(starts_any(code, prefixes) for code in dx[1:]))
    return count, int(count >= 2)


def procedure_day_summary(candidate: Candidate, cb: dict) -> dict[str, object]:
    matched = [i for i, code in enumerate(candidate.pr, 1) if starts_any(code, cb["exposure"]["cholecystectomy_any_pr_prefix"])]
    days = [candidate.prday[i - 1] for i in matched]
    valid = [x for x in days if x is not None and 0 <= x <= candidate.los]
    return {
        "chole_prday_min": min(valid) if valid else None,
        "chole_prday_all_missing_or_invalid": int(bool(matched) and not valid),
        "chole_prday_after_los": int(any(x is not None and x > candidate.los for x in days)),
        "chole_prday_negative": int(any(x is not None and x < 0 for x in days)),
    }


def candidate_record(
    candidate: Candidate,
    cohort: str,
    year: int,
    cb: dict,
    severity: dict[str, dict[str, object]],
    hospitals: dict[str, dict[str, object]],
    ccr: dict[str, dict[str, object]],
) -> dict[str, object]:
    hosp_raw = nstr(candidate.fields.get("HOSP_NRD"))
    hosp = hospitals.get(hosp_raw, {})
    sev = severity.get(candidate.key, {})
    cost = ccr.get(hosp_raw, {})
    frailty_count, frailty_ge2 = fraud_proxy(candidate.dx, cb)
    record: dict[str, object] = {
        "year": year,
        "cohort": cohort,
        "index_id": hashed_id(year, candidate.key, "index"),
        "person_id": hashed_id(year, candidate.visit, "person"),
        "hospital_cluster": hashed_id(year, hosp_raw, "hospital") if hosp_raw else None,
        "stratum_id": f"{year}:{nstr(candidate.fields.get('NRD_STRATUM'))}",
        "A": candidate.A,
        "AGE": as_int(candidate.fields.get("AGE")),
        "FEMALE": as_int(candidate.fields.get("FEMALE")),
        "PAY1": as_int(candidate.fields.get("PAY1")),
        "ZIPINC_QRTL": as_int(candidate.fields.get("ZIPINC_QRTL")),
        "PL_NCHS": as_int(candidate.fields.get("PL_NCHS")),
        "RESIDENT": as_int(candidate.fields.get("RESIDENT")),
        "HCUP_ED": as_int(candidate.fields.get("HCUP_ED")),
        "ELECTIVE": as_int(candidate.fields.get("ELECTIVE")),
        "AWEEKEND": as_int(candidate.fields.get("AWEEKEND")),
        "DMONTH": as_int(candidate.fields.get("DMONTH")),
        "DIED": as_int(candidate.fields.get("DIED")),
        "DISCWT": as_float(candidate.fields.get("DISCWT")),
        "LOS": candidate.los,
        "TOTCHG": as_float(candidate.fields.get("TOTCHG")),
        "DISPUNIFORM": as_int(candidate.fields.get("DISPUNIFORM")),
        "REHABTRANSFER": as_int(candidate.fields.get("REHABTRANSFER")),
        "HOSP_BEDSIZE": as_int(hosp.get("HOSP_BEDSIZE")),
        "HOSP_URCAT4": as_int(hosp.get("HOSP_URCAT4")),
        "HOSP_UR_TEACH": as_int(hosp.get("HOSP_UR_TEACH")),
        "H_CONTRL": as_int(hosp.get("H_CONTRL")),
        "APRDRG": as_int(sev.get("APRDRG")),
        "APRDRG_Severity": as_int(sev.get("APRDRG_Severity")),
        "APRDRG_Risk_Mortality": as_int(sev.get("APRDRG_Risk_Mortality")),
        "CCR_NRD": as_float(cost.get("CCR_NRD")),
        "WAGEINDEX": as_float(cost.get("WAGEINDEX")),
        "facility_cost_nominal": (
            as_float(candidate.fields.get("TOTCHG")) * as_float(cost.get("CCR_NRD"))
            if as_float(candidate.fields.get("TOTCHG")) is not None and as_float(cost.get("CCR_NRD")) is not None
            else None
        ),
        "prior_admissions_90d": candidate.prior_admissions_90d,
        "prior_biliary_90d": candidate.prior_biliary_90d,
        "prior_pancreatitis_90d": candidate.prior_pancreatitis_90d,
        "complete_90d_lookback_proxy": int((as_int(candidate.fields.get("DMONTH")) or 0) >= 4),
        "same_day_records": candidate.same_day_records,
        "overlapping_records": candidate.overlapping_records,
        "index_ercp_related": int(any(ercp_related(x, cb) for x in candidate.pr if x)),
        "frailty_domain_count_nonvalidated": frailty_count,
        "frailty_ge2_nonvalidated": frailty_ge2,
    }
    record.update(procedure_day_summary(candidate, cb))
    for name, prefixes in cb["biliary_phenotypes"].items():
        record[name] = int(any(starts_any(code, prefixes) for code in candidate.dx))
    for i, code in enumerate(candidate.dx, 1):
        record[f"DX{i}"] = code or None
    for window in WINDOWS:
        for name in (
            "all_cause", "primary_middle", "narrow_principal", "wide_any_dx",
            "principal_k85", "principal_k851", "subsequent_cholecystectomy", "subsequent_ercp_related",
        ):
            record[f"y{window}_{name}"] = int(candidate.outcomes.get(f"y{window}_{name}", 0))
            record[f"y{window}_{name}_gap0"] = int(candidate.outcomes.get(f"y{window}_{name}_gap0", 0))
    return record


def atomic_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pq.write_table(pa.Table.from_pylist(rows), name, compression="zstd")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        Path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--year", type=int, choices=EXPECTED_CORE, required=True)
    parser.add_argument("--seven-zip", default="7z")
    parser.add_argument("--progress-every", type=int, default=1_000_000)
    args = parser.parse_args()
    root, year = args.root.resolve(), args.year
    cb_path = root / "02_codebook" / "codebook_v2.0.json"
    cb = json.loads(cb_path.read_text(encoding="utf-8"))
    secrets = json.loads((root / "00_admin" / ".nrd_credentials.json").read_text(encoding="utf-8"))
    password = secrets["archive_passwords"][str(year)]
    layouts = {
        kind: Layout.from_spec(root / "00_admin" / "bootstrap" / f"{year}_{kind}.json")
        for kind in ("core", "severity", "hospital")
    }
    archives = archive_paths(root, year)
    started = time.time()
    stores, qc = index_pass(archives["core"], layouts["core"], password, args.seven_zip, year, cb, args.progress_every)
    qc["timeline_qc"] = timeline_pass(archives["core"], layouts["core"], password, args.seven_zip, year, cb, stores, args.progress_every)
    keys = {candidate.key for store in stores.values() for candidate in store.values()}
    severity, severity_rows = load_lookup(
        archives["severity"], layouts["severity"], password, args.seven_zip,
        "KEY_NRD", keys, EXPECTED_CORE[year],
    )
    hospitals, hospital_rows = load_lookup(
        archives["hospital"], layouts["hospital"], password, args.seven_zip,
        "HOSP_NRD", None, None,
    )
    ccr = load_ccr(archives["ccr"], password, args.seven_zip)
    output_rows: dict[str, int] = {}
    exposure_counts: dict[str, dict[str, int]] = {}
    for cohort, store in stores.items():
        rows = [candidate_record(candidate, cohort, year, cb, severity, hospitals, ccr) for candidate in store.values()]
        rows.sort(key=lambda item: str(item["index_id"]))
        target = root / "03_etl" / f"year={year}" / f"{cohort}_cohort.parquet"
        atomic_parquet(target, rows)
        output_rows[cohort] = len(rows)
        exposure_counts[cohort] = {"A0": sum(x["A"] == 0 for x in rows), "A1": sum(x["A"] == 1 for x in rows)}
    qc.update({
        "year": year,
        "codebook_id": cb["codebook_id"],
        "official_core_rows": EXPECTED_CORE[year],
        "severity_rows": severity_rows,
        "severity_unique_matches": len(severity),
        "hospital_rows": hospital_rows,
        "hospital_lookup_size": len(hospitals),
        "ccr_lookup_size": len(ccr),
        "output_rows": output_rows,
        "exposure_counts": exposure_counts,
        "elapsed_seconds": round(time.time() - started, 3),
        "patient_level_location": "restricted_server_only",
    })
    atomic_json(root / "04_qc" / f"etl_qc_{year}.json", qc)
    print(json.dumps({"status": "PASS", "year": year, "rows": output_rows, "elapsed_seconds": qc["elapsed_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
