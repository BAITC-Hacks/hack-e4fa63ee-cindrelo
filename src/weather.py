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


def validated_frame(payload, expected=None):
    """Validate provider data before publishing it to a reusable cache."""
    try:
        units = payload["hourly_units"]
        if any(units[v] != "m/s" for v in VARIABLES[:2]) or units["temperature_2m"] != "°C" or units["wind_direction_100m"] != "°":
            raise ValueError("Unexpected weather units")
        frame = pd.DataFrame(payload["hourly"]).rename(columns={"time": "valid_time"})
        frame["valid_time"] = pd.to_datetime(frame.valid_time, utc=True)
        if frame.empty or frame.valid_time.isna().any():
            raise ValueError("Missing weather timestamps")
        if frame.valid_time.duplicated().any():
            raise ValueError("Duplicate weather times")
        if not frame.valid_time.eq(frame.valid_time.dt.floor("h")).all():
            raise ValueError("Weather timestamps must be hourly interval starts")
        frame[VARIABLES] = frame[VARIABLES].apply(pd.to_numeric, errors="raise")
        if not np.isfinite(frame[VARIABLES].to_numpy(dtype=float)).all():
            raise ValueError("Nonfinite weather values")
        if (frame[VARIABLES[:2]] < 0).any().any():
            raise ValueError("Negative forecast wind speed")
        frame = frame.set_index("valid_time")
        if expected is not None:
            frame = frame.reindex(expected)
            if not np.isfinite(frame[VARIABLES].to_numpy(dtype=float)).all():
                raise ValueError("Weather does not cover the complete 48-hour horizon")
        latitude, longitude = float(payload["latitude"]), float(payload["longitude"])
        if not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Invalid weather grid coordinates")
        frame.index.name = "valid_time"
        return frame.reset_index()
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed weather response") from exc


class Weather:
    def __init__(self, cfg, offline=False, cache_dir=None, *, refresh=False,
                 fallback_cache_dirs=(), stable_hashes=False, timeout=45, attempts=3):
        self.cfg, self.offline = cfg, offline
        self.cache_dir = ROOT / "data/weather" if cache_dir is None else cache_dir
        self.refresh = refresh
        self.fallback_cache_dirs = fallback_cache_dirs
        self.stable_hashes = stable_hashes
        self.timeout, self.attempts = timeout, attempts
        self.retrievals = []

    def fetch(self, turbine, run, expected=None):
        run = utc(run)
        point = self.cfg["turbines"][turbine]
        params = {"latitude": point["latitude"], "longitude": point["longitude"],
                  "run": run.strftime("%Y-%m-%dT%H:%M"), "models": self.cfg["weather_model"],
                  "hourly": ",".join(VARIABLES), "wind_speed_unit": "ms", "forecast_days": 4}
        path = self.cache_dir / (digest(params) + ".json")
        cached = None
        for candidate in [path, *[directory / path.name for directory in self.fallback_cache_dirs]]:
            if candidate.exists():
                cached = json.loads(candidate.read_text(encoding="utf-8-sig"))
                if cached["request"] != params:
                    raise ValueError(f"Weather cache request mismatch: {candidate.name}")
                if cached["content_hash"] != digest(cached["response"]):
                    raise ValueError(f"Weather cache checksum mismatch: {candidate.name}")
                break
        if cached is not None and (self.offline or not self.refresh):
            self.retrievals.append({"turbine_id": turbine, "run_time": iso(run), "source": "cache"})
            return cached
        if self.offline:
            raise FileNotFoundError(f"No cached weather for {turbine} at {iso(run)}")
        url = ENDPOINT + "?" + urlencode(params)
        for attempt in range(self.attempts):
            try:
                with urlopen(url, timeout=self.timeout) as response:
                    payload = json.load(response)
                if "hourly" not in payload:
                    raise ValueError("Provider returned no hourly weather")
                # Never replace a verified archive with an incomplete/invalid
                # HTTP 200 response. Horizon requests supply their exact times.
                validated_frame(payload, expected)
                envelope = {"request": params, "url": url, "run_time": iso(run),
                            "retrieved_at": iso(pd.Timestamp.now(tz="UTC")),
                            "content_hash": digest(payload), "response": payload}
                write_json(path, envelope)
                self.retrievals.append({"turbine_id": turbine, "run_time": iso(run), "source": "external"})
                time.sleep(0.15)
                return envelope
            except (OSError, ValueError) as exc:
                if attempt == self.attempts - 1:
                    if self.refresh and cached is not None:
                        validated_frame(cached["response"], expected)
                        self.retrievals.append({"turbine_id": turbine, "run_time": iso(run),
                                                "source": "cached_fallback", "reason": type(exc).__name__})
                        return cached
                    raise
                time.sleep(2 ** attempt)

    def horizon(self, issued_at, run=None):
        self.retrievals = []
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
            envelope = self.fetch(turbine, run, expected)
            payload = envelope["response"]
            frame = validated_frame(payload, expected)
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
        if self.stable_hashes:
            # Provider generation duration/retrieval timestamps are not model inputs.
            # Refreshing identical forecast values must retain their identity.
            fields = ["valid_time", "turbine_id", "weather_run_time", "grid_latitude", "grid_longitude", *VARIABLES]
            identity = result[fields].copy()
            for column in ["valid_time", "weather_run_time"]:
                identity[column] = identity[column].map(iso)
            result["weather_hash"] = digest(identity.to_dict("list"))
        else:
            result["weather_hash"] = digest(hashes)
        return result
