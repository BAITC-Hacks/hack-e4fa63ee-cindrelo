import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from .common import ROOT, digest, iso, utc, write_json

VARIABLES = ["wind_speed_10m", "wind_speed_100m", "wind_direction_100m", "temperature_2m"]
ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"


def eligible_run(issued_at, delay_hours):
    if delay_hours < 0:
        raise ValueError("Publication delay cannot be negative")
    return (utc(issued_at) - pd.Timedelta(hours=delay_hours)).floor("12h")


class Weather:
    def __init__(self, cfg, offline=False, cache_dir=None):
        self.cfg, self.offline = cfg, offline
        self.cache_dir = ROOT / "data/weather" if cache_dir is None else cache_dir

    def fetch(self, turbine, run):
        run = utc(run)
        point = self.cfg["turbines"][turbine]
        params = {"latitude": point["latitude"], "longitude": point["longitude"],
                  "run": run.strftime("%Y-%m-%dT%H:%M"), "models": self.cfg["weather_model"],
                  "hourly": ",".join(VARIABLES), "wind_speed_unit": "ms", "forecast_days": 4}
        path = self.cache_dir / (digest(params) + ".json")
        if path.exists():
            envelope = json.loads(path.read_text(encoding="utf-8-sig"))
            if envelope["content_hash"] != digest(envelope["response"]):
                raise ValueError(f"Weather cache checksum mismatch: {path.name}")
            return envelope
        if self.offline:
            raise FileNotFoundError(f"No cached weather for {turbine} at {iso(run)}")
        url = ENDPOINT + "?" + urlencode(params)
        for attempt in range(3):
            try:
                with urlopen(url, timeout=45) as response:
                    payload = json.load(response)
                if "hourly" not in payload:
                    raise ValueError("Provider returned no hourly weather")
                envelope = {"request": params, "url": url, "run_time": iso(run),
                            "retrieved_at": iso(pd.Timestamp.now(tz="UTC")),
                            "content_hash": digest(payload), "response": payload}
                write_json(path, envelope)
                time.sleep(0.15)
                return envelope
            except (OSError, ValueError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def horizon(self, issued_at, run=None):
        issue = utc(issued_at)
        if issue != issue.floor("h"):
            raise ValueError("Issuance must be on the hour")
        run = eligible_run(issue, self.cfg["weather_delay_hours"]) if run is None else utc(run)
        available = run + pd.Timedelta(hours=self.cfg["weather_delay_hours"])
        if available > issue:
            raise ValueError("Future weather availability: temporal leakage rejected")
        expected = pd.date_range(issue, periods=48, freq="h")
        frames, hashes = [], []
        for turbine in self.cfg["turbines"]:
            envelope = self.fetch(turbine, run)
            payload = envelope["response"]
            units = payload["hourly_units"]
            if any(units[v] != "m/s" for v in VARIABLES[:2]) or units["temperature_2m"] != "°C" or units["wind_direction_100m"] != "°":
                raise ValueError("Unexpected weather units")
            frame = pd.DataFrame(payload["hourly"]).rename(columns={"time": "valid_time"})
            frame["valid_time"] = pd.to_datetime(frame.valid_time, utc=True)
            if frame.valid_time.duplicated().any():
                raise ValueError("Duplicate weather times")
            frame = frame.set_index("valid_time").reindex(expected)
            if not np.isfinite(frame[VARIABLES].to_numpy(dtype=float)).all():
                raise ValueError("Weather does not cover the complete 48-hour horizon")
            if (frame[VARIABLES[:2]] < 0).any().any():
                raise ValueError("Negative forecast wind speed")
            frame.index.name = "valid_time"
            frame = frame.reset_index()
            frame["turbine_id"] = turbine
            frame["issued_at"] = issue
            frame["weather_run_time"] = run
            frame["weather_available_at"] = available
            frame["horizon_hours"] = np.arange(1, 49)
            frame["grid_latitude"] = payload["latitude"]
            frame["grid_longitude"] = payload["longitude"]
            frames.append(frame)
            hashes.append(envelope["content_hash"])
        result = pd.concat(frames, ignore_index=True)
        result["weather_hash"] = digest(hashes)
        return result
