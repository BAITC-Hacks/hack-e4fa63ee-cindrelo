"""Acquire immutable fixed-lead forecast archives, never day-zero/reanalysis."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import pandas as pd
from .common import config, digest, write_json, write_csv

VARIABLES=["wind_speed_10m","wind_speed_100m","wind_direction_10m","temperature_2m","surface_pressure","relative_humidity_2m","cloud_cover","precipitation"]
MODELS=["gfs_global","icon_global","jma_gsm"]


def acquire(offset_days=3, output="outputs/provider-weather"):
    if offset_days not in (2, 3):
        raise ValueError("This research only supports 48 or 72 hour offsets")
    cfg=config();root=Path("data/provider-weather");root.mkdir(parents=True,exist_ok=True)
    frames=[]
    for turbine,point in cfg["turbines"].items():
        for start in pd.date_range("2025-06-01","2026-01-01",freq="MS"):
            end=start+pd.offsets.MonthEnd(0)
            params={"latitude":point["latitude"],"longitude":point["longitude"],"start_date":str(start.date()),"end_date":str(end.date()),
                    "hourly":",".join(v+f"_previous_day{offset_days}" for v in VARIABLES),"models":",".join(MODELS),"wind_speed_unit":"ms","timezone":"UTC"}
            path=root/(digest(params)+".json")
            if path.exists():
                record=json.loads(path.read_text(encoding="utf-8"))
                if digest(record["response"])!=record["content_hash"]: raise ValueError("Provider cache checksum mismatch")
            else:
                url="https://previous-runs-api.open-meteo.com/v1/forecast?"+urlencode(params)
                with urlopen(url,timeout=60) as response:payload=json.load(response)
                record={"request":params,"url":url,"retrieved_at":pd.Timestamp.now(tz="UTC").isoformat(),"response":payload,"content_hash":digest(payload)}
                write_json(path,record)
            frame=pd.DataFrame(record["response"]["hourly"]).rename(columns={"time":"valid_time"})
            frame["valid_time"]=pd.to_datetime(frame.valid_time,utc=True);frame["turbine_id"]=turbine
            frames.append(frame);print(turbine,start.date(),len(frame),flush=True)
    result=pd.concat(frames,ignore_index=True)
    if result.duplicated(["turbine_id","valid_time"]).any():raise ValueError("Duplicate archive keys")
    write_csv(Path(output)/"weather.csv",result)
    write_json(Path(output)/"provenance.json",{"source":"Open-Meteo Previous Runs","offset_hours":offset_days*24,"models":MODELS,"variables":VARIABLES,
               "assumed_publication_delay_hours":8,"exact_run_initialization_and_publication_not_returned":True,
               "eligibility":"Require valid_time - offset_hours + 8h <= issue for every feature; conditional on documented offset semantics.",
               "no_operational_as_issued_claim":True})


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--offset-days",type=int,choices=[2,3],default=3)
    parser.add_argument("--output",default="outputs/provider-weather")
    args=parser.parse_args()
    if args.offset_days != 3 and args.output == "outputs/provider-weather":
        parser.error("Choose a separate output directory for the alternative offset")
    acquire(args.offset_days,args.output)
