"""Runtime weather inputs for the selected AIFS/GEM wind candidate.

Previous Runs exposes lead offsets, not original publication timestamps. Every
selected value therefore has *conditional* availability: valid time minus its
48/72-hour offset plus an assumed eight-hour publication delay. This module
does not import fitting code or consult observations.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from .common import ROOT, digest, iso, utc, write_json
from .weather import VARIABLES as BASE_VARIABLES, Weather

ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
PUBLICATION_DELAY_HOURS = 8
MODELS = ["gfs_global", "icon_global", "jma_gsm"]
VARIABLES = ["wind_speed_10m", "wind_speed_100m", "wind_direction_10m",
             "temperature_2m", "surface_pressure", "relative_humidity_2m",
             "cloud_cover", "precipitation"]
COMMON_VARIABLES = ["temperature_2m", "surface_pressure", "relative_humidity_2m",
                    "cloud_cover", "precipitation"]
EXTRA_VARIABLES = {
    "ecmwf_aifs025_single": ["wind_speed_10m", "wind_speed_100m",
                             "wind_direction_10m", "wind_direction_100m", *COMMON_VARIABLES],
    "cmc_gem_gdps": ["wind_speed_10m", "wind_speed_80m", "wind_speed_120m",
                     "wind_direction_10m", "wind_direction_80m", "wind_direction_120m",
                     *COMMON_VARIABLES],
}
GROUPS = {"provider": {model: VARIABLES for model in MODELS}, "extra": EXTRA_VARIABLES}
BUNDLED_CACHE = ROOT / "examples/candidate/weather"
UNSUPPORTED = {("jma_gsm", "wind_speed_100m")}


def _unit(variable):
    if variable.startswith("wind_speed"):
        return "m/s"
    if variable.startswith("wind_direction"):
        return "°"
    return {"temperature_2m": "°C", "surface_pressure": "hPa",
            "relative_humidity_2m": "%", "cloud_cover": "%", "precipitation": "mm"}[variable]


def _validate_values(values, variable):
    values = pd.to_numeric(values, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"Missing/nonfinite selected candidate weather: {variable}")
    if (variable.startswith("wind_speed") or variable == "precipitation") and (values < 0).any():
        raise ValueError(f"Negative candidate weather: {variable}")
    if variable.startswith("wind_direction") and ((values < 0) | (values > 360)).any():
        raise ValueError("Invalid candidate wind direction")
    if variable in {"relative_humidity_2m", "cloud_cover"} and ((values < 0) | (values > 100)).any():
        raise ValueError(f"Invalid candidate percentage: {variable}")
    if variable == "surface_pressure" and (values <= 0).any():
        raise ValueError("Invalid candidate surface pressure")
    return values


def validated_previous_runs(payloads, cfg, issued_at, group):
    """Select first, validate selected values, and discard all raw offsets.

    Unavailable values cannot affect either features or the canonical forecast
    hash. The one known unsupported JMA field stays null, as in training.
    """
    issue = utc(issued_at)
    expected = pd.date_range(issue, periods=48, freq="h")
    if not isinstance(payloads, list) or len(payloads) != len(cfg["turbines"]):
        raise ValueError("Unexpected candidate multi-location response")
    use_day2 = expected - pd.Timedelta(hours=48 - PUBLICATION_DELAY_HOURS) <= issue
    offsets = np.where(use_day2, 48, 72)
    bounds = expected - pd.to_timedelta(offsets, unit="h") + pd.Timedelta(hours=PUBLICATION_DELAY_HOURS)
    if (bounds > issue).any():
        raise ValueError("No eligible candidate weather offset")
    frames, grids = [], []
    try:
        for turbine, payload in zip(cfg["turbines"], payloads):
            latitude, longitude = float(payload["latitude"]), float(payload["longitude"])
            if not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("Invalid candidate grid coordinates")
            if payload.get("utc_offset_seconds", 0) != 0:
                raise ValueError("Candidate response must use UTC")
            raw = pd.DataFrame(payload["hourly"]).rename(columns={"time": "valid_time"})
            raw["valid_time"] = pd.to_datetime(raw.valid_time, utc=True)
            if (raw.empty or raw.valid_time.isna().any() or raw.valid_time.duplicated().any()
                    or not raw.valid_time.eq(raw.valid_time.dt.floor("h")).all()):
                raise ValueError("Invalid candidate hourly timestamps")
            if not expected.isin(raw.valid_time).all():
                raise ValueError("Candidate weather does not cover the complete 48-hour horizon")
            raw = raw.set_index("valid_time").loc[expected]
            selected = pd.DataFrame({"turbine_id": turbine, "valid_time": expected,
                                     f"{group}_offset_hours": offsets,
                                     f"{group}_available_bound": bounds})
            for model, variables in GROUPS[group].items():
                for variable in variables:
                    output = f"{group}_{variable}_{model}"
                    if (model, variable) in UNSUPPORTED:
                        selected[output] = np.nan
                        continue
                    values = np.empty(len(expected), dtype=float)
                    for day, mask in [(2, use_day2), (3, ~use_day2)]:
                        column = f"{variable}_previous_day{day}_{model}"
                        if payload["hourly_units"].get(column) != _unit(variable):
                            raise ValueError(f"Unexpected candidate weather units: {column}")
                        values[mask] = _validate_values(raw.loc[mask, column], variable)
                    selected[output] = values
            frames.append(selected)
            grids.append({"turbine_id": turbine, "latitude": latitude, "longitude": longitude})
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed candidate weather response") from exc
    return pd.concat(frames, ignore_index=True), grids


def _canonical_rows(frame, columns):
    identity = frame.sort_values(["turbine_id", "valid_time"])[columns].copy()
    for column in identity:
        if isinstance(identity[column].dtype, pd.DatetimeTZDtype):
            identity[column] = identity[column].map(iso)
    return identity.astype(object).where(pd.notna(identity), None).to_dict("records")


class CandidateWeather:
    """Weather-compatible provider with isolated cache and immutable demo fallback."""

    def __init__(self, cfg, offline=False, cache_dir=None, *, refresh=False,
                 fallback_cache_dirs=(), stable_hashes=True, timeout=12, attempts=1,
                 base_provider=None):
        if not isinstance(attempts, int) or not 1 <= attempts <= 3 or not 0 < timeout <= 60:
            raise ValueError("Candidate weather requires 1-3 attempts and a timeout of at most 60 seconds")
        self.cfg, self.offline, self.refresh = cfg, offline, refresh
        self.cache_dir = Path(cache_dir) if cache_dir is not None else ROOT / "outputs/dashboard-weather-cache/candidate"
        if self.cache_dir.resolve().is_relative_to(BUNDLED_CACHE.resolve()):
            raise ValueError("Bundled candidate weather is immutable; use a runtime cache directory")
        self.fallback_cache_dirs = tuple(Path(p) for p in fallback_cache_dirs) + (BUNDLED_CACHE,)
        self.timeout, self.attempts = timeout, attempts
        # All candidate hashes are canonical even if an older caller passes False.
        self.stable_hashes = True
        self.base = base_provider if base_provider is not None else Weather(
            cfg, offline=offline, cache_dir=self.cache_dir / "base", refresh=refresh,
            fallback_cache_dirs=(*self.fallback_cache_dirs, ROOT / "data/weather", ROOT / "examples/backend/weather"),
            stable_hashes=True, timeout=timeout, attempts=attempts)
        self.retrievals = []
        self.provenance = {}

    def request_params(self, group, issued_at):
        issue = utc(issued_at)
        variables = list(dict.fromkeys(v for variables in GROUPS[group].values() for v in variables))
        return {"latitude": ",".join(str(p["latitude"]) for p in self.cfg["turbines"].values()),
                "longitude": ",".join(str(p["longitude"]) for p in self.cfg["turbines"].values()),
                "start_date": str(issue.date()), "end_date": str((issue + pd.Timedelta(hours=47)).date()),
                "hourly": ",".join(f"{v}_previous_day{day}" for day in [2, 3] for v in variables),
                "models": ",".join(GROUPS[group]), "timezone": "UTC", "wind_speed_unit": "ms"}

    def fetch_group(self, group, issued_at):
        params = self.request_params(group, issued_at)
        path = self.cache_dir / (digest(params) + ".json")
        cached = None
        cache_error = None
        for candidate in [path, *[p / path.name for p in self.fallback_cache_dirs]]:
            if not candidate.exists():
                continue
            try:
                record = json.loads(candidate.read_text(encoding="utf-8-sig"))
                if record["request"] != params or digest(record["response"]) != record["content_hash"]:
                    raise ValueError("Candidate weather cache request/checksum mismatch")
                selected, grids = validated_previous_runs(record["response"], self.cfg, issued_at, group)
                cached = (record, selected, grids)
                break
            except (OSError, ValueError, KeyError, TypeError) as exc:
                cache_error = exc
        if cached is not None and (self.offline or not self.refresh):
            self.retrievals.append({"group": group, "source": "cache", "request_hash": digest(params)})
            return cached
        if self.offline:
            if cache_error is not None:
                raise ValueError("No valid cached candidate weather") from cache_error
            raise FileNotFoundError(f"No cached candidate {group} weather for {iso(issued_at)}")
        url = ENDPOINT + "?" + urlencode(params)
        for attempt in range(self.attempts):
            try:
                with urlopen(url, timeout=self.timeout) as response:
                    payload = json.load(response)
                selected, grids = validated_previous_runs(payload, self.cfg, issued_at, group)
                record = {"request": params, "url": url, "retrieved_at": iso(pd.Timestamp.now(tz="UTC")),
                          "response": payload, "content_hash": digest(payload)}
                # HTTP 200 is insufficient: only complete validated selected data
                # can replace a previous cache entry. Never write bundled files.
                write_json(path, record)
                self.retrievals.append({"group": group, "source": "external", "request_hash": digest(params)})
                return record, selected, grids
            except (OSError, ValueError) as exc:
                if attempt + 1 == self.attempts:
                    if cached is not None:
                        self.retrievals.append({"group": group, "source": "cached_fallback",
                                                "request_hash": digest(params), "reason": type(exc).__name__})
                        return cached
                    raise
                time.sleep(2 ** attempt)

    def horizon(self, issued_at, run=None):
        issue = utc(issued_at)
        if pd.isna(issue) or issue != issue.floor("h"):
            raise ValueError("Issuance must be on the hour")
        self.retrievals, self.provenance = [], {}
        result = self.base.horizon(issue, run)
        self.retrievals.extend({**r, "group": "base_ifs"} for r in getattr(self.base, "retrievals", []))
        requests, grids = [], {}
        for group in GROUPS:
            record, selected, grids[group] = self.fetch_group(group, issue)
            result = result.merge(selected, on=["turbine_id", "valid_time"], how="left", validate="one_to_one")
            requests.append({"group": group, "request": record["request"], "url": record["url"],
                             "response_content_hash": record["content_hash"],
                             "retrieved_at": record.get("retrieved_at"),
                             "archive_sources": record.get("archive_sources", [])})
        assumptions = {"source": "Open-Meteo Previous Runs", "endpoint": ENDPOINT,
                       "publication_provenance_verified": False,
                       "assumed_publication_delay_hours": PUBLICATION_DELAY_HOURS,
                       "eligibility": "valid_time - offset_hours + 8h <= issued_at; prefer 48h, otherwise 72h",
                       "base_model": self.cfg["weather_model"],
                       "base_publication_delay_hours": self.cfg["weather_delay_hours"],
                       "models_and_variables": GROUPS,
                       "native_time_steps": {"ecmwf_aifs025_single": "6-hourly, API interpolates hourly",
                                             "cmc_gem_gdps": "3-hourly, API interpolates hourly"},
                       "unsupported_fields": ["provider_wind_speed_100m_jma_gsm"]}
        columns = ["turbine_id", "valid_time", "issued_at", "horizon_hours", "weather_run_time",
                   "weather_available_at", "grid_latitude", "grid_longitude", *BASE_VARIABLES]
        columns += [c for c in result if c.startswith(("provider_", "extra_"))]
        identity = {"schema_version": 1, "assumptions": assumptions,
                    "requested_sites": {t: {k: p[k] for k in ["latitude", "longitude"]}
                                        for t, p in self.cfg["turbines"].items()},
                    "provider_grids": grids, "rows": _canonical_rows(result, columns)}
        result["weather_hash"] = digest(identity)
        self.provenance = {**assumptions, "requests": requests, "provider_grids": grids,
                           "selected_content_hash": result.weather_hash.iloc[0],
                           "base_retrievals": [r for r in self.retrievals if r["group"] == "base_ifs"]}
        result.attrs["candidate_weather_provenance"] = self.provenance
        return result
