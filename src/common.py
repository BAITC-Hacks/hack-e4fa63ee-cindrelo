import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FORECAST_COLUMNS = "forecast_id issued_at valid_time turbine_id horizon_hours power_normalized weather_run_time weather_available_at model_version input_hash status".split()
METRIC_COLUMNS = "model turbine_id horizon_bucket mae rmse n_samples period_start period_end".split()


def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


def utc(value):
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        raise ValueError(f"Explicit timezone required: {value}")
    return t.tz_convert("UTC")


def iso(value):
    return utc(value).isoformat().replace("+00:00", "Z")


def local_date(value, cfg):
    return pd.Timestamp(value).tz_localize(cfg["calendar_timezone"]).tz_convert("UTC")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_csv(path, frame):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".csv.tmp")
    copy = frame.copy()
    for col in copy:
        if isinstance(copy[col].dtype, pd.DatetimeTZDtype):
            copy[col] = copy[col].map(iso, na_action="ignore")
    copy.to_csv(temp, index=False, encoding="utf-8")
    os.replace(temp, path)


def warnings(cfg):
    return [
        f"Raw timestamps assumed {cfg['raw_timezone']}, interval starts; organizer confirmation pending.",
        f"Weather availability assumed initialization + {cfg['weather_delay_hours']} hours; historical publication provenance unverified.",
        "Open-Meteo ECMWF IFS archive may contain hindcasts; as-issued compliance is not established.",
        "Power is normalized, not MW/MWh; February actuals are absent from supplied data.",
        "Weather: Open-Meteo (https://open-meteo.com/), ECMWF; CC BY 4.0 attribution.",
    ]
