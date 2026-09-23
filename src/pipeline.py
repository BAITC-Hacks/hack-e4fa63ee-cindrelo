import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, FORECAST_COLUMNS, METRIC_COLUMNS, digest, iso, utc, warnings, write_csv, write_json
from .data import load_hourly
from .model import bundle_for_issue, predict
from .weather import Weather, eligible_run


def validate_forecasts(frame):
    if frame.empty or frame.duplicated(["forecast_id", "turbine_id", "valid_time"]).any():
        raise ValueError("Empty or duplicate forecast output")
    if not np.isfinite(frame.power_normalized).all() or not frame.power_normalized.between(0, 1).all():
        raise ValueError("Forecast power must be finite in [0,1]")
    if (frame.weather_available_at > frame.issued_at).any():
        raise ValueError("Weather availability exceeds issuance")
    if (frame.weather_run_time > frame.weather_available_at).any():
        raise ValueError("Weather initialization exceeds availability")
    for _, group in frame.groupby("forecast_id"):
        if set(group.turbine_id) != {"turbine_1", "turbine_2"} or group.issued_at.nunique() != 1:
            raise ValueError("Each forecast must contain both turbines and one issuance")
    expected = (frame.valid_time - frame.issued_at).dt.total_seconds() / 3600 + 1
    if not (expected == frame.horizon_hours).all():
        raise ValueError("Incorrect horizon numbering")
    for _, group in frame.groupby(["forecast_id", "turbine_id"]):
        if sorted(group.horizon_hours.tolist()) != list(range(1, 49)):
            raise ValueError("Incomplete 48-hour horizon")


def read_forecasts(directory):
    path = Path(directory) / "forecasts.csv"
    if not path.exists():
        return pd.DataFrame(columns=FORECAST_COLUMNS)
    frame = pd.read_csv(path, encoding="utf-8-sig")
    for col in ["issued_at", "valid_time", "weather_run_time", "weather_available_at"]:
        frame[col] = pd.to_datetime(frame[col], utc=True)
    return frame


class ForecastRun:
    """Stateful guarded tools shared by deterministic and LLM controllers."""

    def __init__(self, cfg, issue, output, offline=False, bundle=None):
        self.cfg, self.issue, self.output = cfg, utc(issue), Path(output)
        self.provider = Weather(cfg, offline)
        self.bundle = bundle if bundle is not None else bundle_for_issue(self.issue, cfg)
        if self.bundle.get("config_hash", digest(cfg)) != digest(cfg):
            raise ValueError("Model config mismatch")
        self.weather = self.forecast = None
        self.validated = self.compared = self.done = False
        self.events = []
        self.forecast_id = "attempt-" + self.issue.strftime("%Y%m%dT%H%MZ")
        self.fetch_count = 0
        self.used_older = False

    def event(self, tool, status, message):
        self.events.append({"forecast_id": self.forecast_id, "timestamp": iso(self.issue),
                            "tool": tool, "status": status, "message": message,
                            "executed_at": iso(pd.Timestamp.now(tz="UTC"))})

    def flush_events(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as file:
            for event in self.events:
                event["forecast_id"] = self.forecast_id
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.events.clear()

    def fetch_weather(self, older_run=False):
        if self.forecast is not None or self.done:
            raise ValueError("Cannot replace weather after prediction")
        if self.fetch_count >= 2:
            raise ValueError("Weather recovery budget exhausted")
        self.fetch_count += 1
        run = eligible_run(self.issue, self.cfg["weather_delay_hours"])
        if older_run:
            run -= pd.Timedelta(hours=12)
        self.weather = self.provider.horizon(self.issue, run)
        self.used_older = older_run
        self.validated = False
        return {"rows": len(self.weather), "run_time": iso(run), "older_run": older_run,
                "provenance": "unverified archive; publication delay assumed"}

    def validate_inputs(self):
        if self.weather is None:
            raise ValueError("Fetch weather first")
        if self.weather.shape[0] != 48 * len(self.cfg["turbines"]):
            raise ValueError("Incomplete weather horizon")
        if utc(self.bundle["trained_until"]) > self.issue:
            raise ValueError("Model training cutoff is in the future")
        self.validated = True
        return {"valid": True, "model": self.bundle["version"], "warnings": warnings(self.cfg)}

    def predict_power(self, fallback=False):
        if not self.validated:
            raise ValueError("Validate inputs before prediction")
        method = "curve" if fallback else self.bundle["selected"]
        identity = digest({"issue": iso(self.issue), "weather": self.weather.weather_hash.iloc[0],
                           "config": self.cfg, "model": self.bundle["version"], "method": method})
        self.forecast_id = self.issue.strftime("%Y%m%dT%H%MZ") + "-" + identity[:12]
        existing = read_forecasts(self.output)
        if self.forecast_id in set(existing.forecast_id):
            self.done = True
            return {"deduplicated": True, "forecast_id": self.forecast_id, "message": "Unchanged inputs; prediction skipped"}
        frame = self.weather.copy()
        frame["power_normalized"] = predict(self.bundle, frame, method)
        frame["forecast_id"] = self.forecast_id
        frame["model_version"] = self.bundle["version"] + ":" + method
        frame["input_hash"] = identity
        # Provenance is unresolved even when model/coverage checks pass.
        frame["status"] = "degraded"
        self.forecast = frame[FORECAST_COLUMNS]
        validate_forecasts(self.forecast)
        return {"forecast_id": self.forecast_id, "model": method, "rows": len(frame),
                "mean_power": float(frame.power_normalized.mean()), "status": "degraded"}

    def compare_forecasts(self):
        if self.forecast is None:
            raise ValueError("Predict before comparing")
        previous = read_forecasts(self.output)
        previous = previous.loc[previous.issued_at <= self.issue] if not previous.empty else previous
        result = {"overlap_rows": 0, "mean_absolute_revision": None}
        if not previous.empty:
            last = previous.sort_values("issued_at", kind="stable").forecast_id.iloc[-1]
            overlap = self.forecast.merge(previous.loc[previous.forecast_id.eq(last)],
                                          on=["turbine_id", "valid_time"], suffixes=("", "_previous"))
            if not overlap.empty:
                result = {"overlap_rows": len(overlap), "previous_forecast_id": last,
                          "mean_absolute_revision": float((overlap.power_normalized - overlap.power_normalized_previous).abs().mean())}
        self.compared = True
        return result

    def publish_forecast(self):
        if not self.compared or self.forecast is None:
            raise ValueError("Predict and compare before publishing")
        previous = read_forecasts(self.output)
        combined = self.forecast if previous.empty else pd.concat([previous, self.forecast], ignore_index=True)
        validate_forecasts(combined)
        actuals = load_hourly(self.cfg)
        actuals = actuals.loc[actuals.complete & actuals.valid_time.between(combined.valid_time.min(), combined.valid_time.max()),
                              ["valid_time", "turbine_id", "power_normalized"]]
        metric_path = ROOT / "artifacts/metrics.csv"
        metrics = pd.read_csv(metric_path, encoding="utf-8-sig") if metric_path.exists() else pd.DataFrame(columns=METRIC_COLUMNS)
        write_csv(self.output / "forecasts.csv", combined)
        write_csv(self.output / "actuals.csv", actuals)
        write_csv(self.output / "metrics.csv", metrics)
        write_json(self.output / "manifest.json", {"schema_version": 1, "data_kind": "model_output",
                    "timezone": "UTC", "target_unit": "normalized_power",
                    "description": "Archived-weather model forecasts; historical provenance remains unverified.",
                    "warnings": warnings(self.cfg), "config": self.cfg})
        self.done = True
        return {"published": True, "forecast_id": self.forecast_id, "directory": str(self.output)}

    def call(self, tool, arguments=None):
        allowed = {"fetch_weather", "validate_inputs", "predict_power", "compare_forecasts", "publish_forecast"}
        if tool not in allowed or self.done:
            raise ValueError("Tool unavailable in current state")
        self.event(tool, "started", "Executing guarded tool")
        try:
            result = getattr(self, tool)(**(arguments or {}))
            self.event(tool, "skipped" if result.get("deduplicated") else "completed", json.dumps(result))
            return result
        except Exception as exc:
            self.event(tool, "failed", str(exc))
            raise


def run_deterministic(run):
    try:
        try:
            run.call("fetch_weather")
        except (OSError, ValueError):
            run.event("controller", "warning", "Latest eligible weather failed; trying preceding run")
            run.call("fetch_weather", {"older_run": True})
        run.call("validate_inputs")
        try:
            result = run.call("predict_power")
        except (ValueError, RuntimeError):
            run.event("controller", "warning", "Main model failed; trying empirical curve")
            result = run.call("predict_power", {"fallback": True})
        if run.done:
            return result
        run.call("compare_forecasts")
        return run.call("publish_forecast")
    finally:
        run.flush_events()
