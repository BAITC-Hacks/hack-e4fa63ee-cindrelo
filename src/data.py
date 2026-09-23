import numpy as np
import pandas as pd

from .common import ROOT, digest, iso, write_csv, write_json


def aggregate(raw, timezone):
    raw = raw.copy()
    raw.columns = ["id", "time", "wind_speed", "power_normalized", "temperature"]
    naive = pd.to_datetime(raw.pop("time"), format="mixed")
    if naive.duplicated().any():
        raise ValueError("Duplicate raw timestamps")
    if ((naive.dt.minute % 10 != 0) | (naive.dt.second != 0)).any():
        raise ValueError("Samples must align to ten-minute boundaries")
    # Kazakhstan's 2024 offset change can create ambiguous wall times. Do not guess.
    times = naive.dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    ambiguous = int(times.isna().sum())
    raw.index = pd.DatetimeIndex(times)
    raw = raw.loc[raw.index.notna()].sort_index()
    numeric = ["wind_speed", "power_normalized", "temperature"]
    raw[numeric] = raw[numeric].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(raw[numeric].to_numpy()).all():
        raise ValueError("Nonfinite observations")
    if not raw.power_normalized.between(0, 1).all() or (raw.wind_speed < 0).any():
        raise ValueError("Invalid physical range")
    grouped = raw[numeric].resample("1h")
    hourly = grouped.mean()
    hourly["sample_count"] = grouped.power_normalized.count()
    hourly["complete"] = hourly.sample_count.eq(6)
    hourly.loc[~hourly.complete, "power_normalized"] = np.nan
    hourly.index.name = "valid_time"
    return hourly.reset_index(), ambiguous


def prepare(cfg):
    frames, report = [], {}
    for turbine, info in cfg["turbines"].items():
        paths = list((ROOT / "docs").glob(info["file_glob"]))
        if len(paths) != 1:
            raise ValueError(f"Expected one source CSV for {turbine}, found {paths}")
        raw = pd.read_csv(paths[0], encoding="utf-8-sig")
        hourly, ambiguous = aggregate(raw, cfg["raw_timezone"])
        hourly["turbine_id"] = turbine
        frames.append(hourly)
        report[turbine] = {"source": paths[0].name, "rows": len(raw),
                           "complete_hours": int(hourly.complete.sum()),
                           "ambiguous_rows_excluded": ambiguous,
                           "first_hour": iso(hourly.valid_time.min()),
                           "last_hour": iso(hourly.valid_time.max())}
    data = pd.concat(frames, ignore_index=True)
    write_csv(ROOT / "data/hourly.csv", data)
    write_json(ROOT / "data/preparation.json", {"config_hash": digest(cfg), "turbines": report})
    return data, report


def load_hourly(cfg):
    metadata = ROOT / "data/preparation.json"
    if metadata.exists():
        import json
        if json.loads(metadata.read_text(encoding="utf-8-sig"))["config_hash"] == digest(cfg):
            data = pd.read_csv(ROOT / "data/hourly.csv", encoding="utf-8-sig")
            data["valid_time"] = pd.to_datetime(data.valid_time, utc=True)
            return data
    return prepare(cfg)[0]
