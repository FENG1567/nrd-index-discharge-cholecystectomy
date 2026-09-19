#!/usr/bin/env python3
"""Create server-only, de-identified flags for 90-day principal-diagnosis components.

The five components partition only qualifying principal diagnoses; a readmission
can contribute to more than one component across the 90-day window, so output
flags are clinically interpretable component risks, not mutually exclusive
patient-level endpoint categories.  Raw data and record-level timelines remain
on the server.  This script emits a single restricted server parquet indexed by
the existing hashed index_id.
"""
from __future__ import annotations
import argparse, importlib.util, json, os, sys, tempfile, time
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

OUTCOMES=("k80","k81","k830","k851","other_k85")

def load_module(root: Path):
    p=root/"03_etl"/"nrd_build_cohort.py"; spec=importlib.util.spec_from_file_location("nrd_etl_for_components",p)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load ETL module")
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod

def atomic_parquet(path: Path, frame: pd.DataFrame):
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=path.name,suffix=".tmp",dir=path.parent); os.close(fd)
    try: pq.write_table(pa.Table.from_pandas(frame,preserve_index=False),tmp,compression="zstd"); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def scan(mod, archive: Path, layout, password: str, seven_zip: str, candidates: dict, year: int):
    by_visit={c.visit:c for c in candidates.values()}; flags={c.key:{x:0 for x in OUTCOMES} for c in candidates.values()}; count=0
    with mod.SevenZipLines(seven_zip,archive,password) as source:
        for number,row in mod.iter_rows(layout,source,1_000_000,f"component scan {year}"):
            visit=mod.nstr(row.get("NRD_VisitLink")); candidate=by_visit.get(visit)
            if candidate is None or mod.nstr(row.get("KEY_NRD"))==candidate.key: continue
            nde,los=mod.as_int(row.get("NRD_DaysToEvent")),mod.as_int(row.get("LOS"))
            if nde is None or los is None or los<0: continue
            gap=nde-candidate.discharge_day
            if gap<1 or gap>90: continue
            count+=1; dx1=mod.nstr(row.get("I10_DX1")); target=flags[candidate.key]
            if dx1.startswith("K80"): target["k80"]=1
            if dx1.startswith("K81"): target["k81"]=1
            if dx1.startswith("K830"): target["k830"]=1
            if dx1.startswith("K851"): target["k851"]=1
            if dx1.startswith("K85") and not dx1.startswith("K851"): target["other_k85"]=1
        if number != mod.EXPECTED_CORE[year]: raise RuntimeError(f"year {year}: core row count {number} differs from official {mod.EXPECTED_CORE[year]}")
    rows=[]
    for c in candidates.values(): rows.append({"index_id":mod.hashed_id(year,c.key,"index"),**{f"y90_component_{x}":flags[c.key][x] for x in OUTCOMES}})
    return pd.DataFrame(rows), {"year":year,"candidates":len(candidates),"followup_records":count,"official_core_rows":number}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",type=Path,required=True); ap.add_argument("--seven-zip",default="7z"); args=ap.parse_args(); root=args.root.resolve(); mod=load_module(root); cb=json.loads((root/"02_codebook"/"codebook_v2.0.json").read_text()); cred=json.loads((root/"00_admin"/".nrd_credentials.json").read_text()); all_rows=[]; qcs=[]
    for year in range(2018,2023):
        started=time.time(); layout=mod.Layout.from_spec(root/"00_admin"/"bootstrap"/f"{year}_core.json"); archive=mod.archive_paths(root,year)["core"]
        stores,index_qc=mod.index_pass(archive,layout,cred["archive_passwords"][str(year)],args.seven_zip,year,cb,1_000_000)
        frame,qc=scan(mod,archive,layout,cred["archive_passwords"][str(year)],args.seven_zip,stores["main"],year); qc["elapsed_seconds"]=round(time.time()-started,3); qc["index_qc_pass1"]=index_qc["pass1_qc"]; all_rows.append(frame); qcs.append(qc); print(json.dumps(qc),flush=True)
    out=pd.concat(all_rows,ignore_index=True)
    if out.index_id.duplicated().any(): raise RuntimeError("duplicated component index IDs")
    # This restricted bridge is deliberately checked on the server.  The
    # individual index identifiers and component flags are never exported.
    analysis=pd.read_parquet(root/"03_etl"/"analysis_ready_main.parquet",columns=["index_id"])
    if len(out)!=len(analysis) or out.index_id.duplicated().any() or analysis.index_id.duplicated().any():
        raise RuntimeError(f"component bridge row-count failure: flags={len(out)}, analysis={len(analysis)}")
    if set(out.index_id)!=set(analysis.index_id):
        raise RuntimeError("component bridge identifier-set failure")
    target=root/"bjs_revision_results"/"component_flags_server_only.parquet"; atomic_parquet(target,out)
    (root/"bjs_revision_results"/"component_flag_qc.json").write_text(json.dumps({"status":"PASS","rows":len(out),"analysis_ready_rows":len(analysis),"one_to_one_bridge":True,"components":list(OUTCOMES),"component_relationship":"Non-mutually-exclusive patient-level 90-day component-risk flags; a patient can have more than one component across different readmissions.","year_qc":qcs,"patient_level_exported":False},indent=2)+"\n")
    print(json.dumps({"status":"PASS","rows":len(out),"target":str(target)}))
if __name__=="__main__": main()
