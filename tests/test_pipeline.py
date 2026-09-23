import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.agent import run_agent
from src.common import config, utc
from src.data import aggregate
from src.model import predict
from src.pipeline import ForecastRun, run_deterministic, validate_forecasts
from src.weather import Weather, eligible_run


def test_csv_nullable_timestamp_roundtrip(tmp_path):
    from src.common import write_csv
    frame = pd.DataFrame({"time": pd.to_datetime(["2026-01-01T00:00Z", None], utc=True)})
    write_csv(tmp_path / "nullable.csv", frame)
    restored = pd.read_csv(tmp_path / "nullable.csv")
    assert restored.time.iloc[0] == "2026-01-01T00:00:00Z"
    assert pd.isna(restored.time.iloc[1])


def test_hourly_gaps_are_not_zero():
    times = pd.date_range("2026-01-01", periods=12, freq="10min").delete(9)
    raw = pd.DataFrame({"id": range(11), "time": times.astype(str), "wind": 6.0, "power": 0.4, "temperature": 1.0})
    hourly, _ = aggregate(raw, "UTC")
    assert hourly.sample_count.tolist() == [6, 5]
    assert hourly.power_normalized.iloc[0] == pytest.approx(0.4)
    assert pd.isna(hourly.power_normalized.iloc[1])


def test_ambiguous_local_hour_excluded():
    raw = pd.DataFrame([[1, "2024-02-29 23:00:00", 4, 0.2, 0]], columns=list("abcde"))
    hourly, excluded = aggregate(raw, "Asia/Almaty")
    assert excluded == 1
    assert hourly.empty


def test_future_weather_rejected_before_fetch(monkeypatch):
    provider = Weather(config(), offline=True)
    monkeypatch.setattr(provider, "fetch", lambda *args: pytest.fail("Should not fetch future weather"))
    with pytest.raises(ValueError, match="Future weather"):
        provider.horizon("2026-02-01T00:00Z", "2026-02-01T00:00Z")
    assert eligible_run("2026-02-01T00:00Z", 8) == utc("2026-01-31T12:00Z")


def test_model_future_training_rejected():
    with pytest.raises(ValueError, match="after forecast issuance"):
        predict({"trained_until": "2026-02-01T00:00Z"}, pd.DataFrame({"issued_at": [utc("2026-01-31T00:00Z")]}))


def test_incomplete_weather_rejected(monkeypatch):
    provider = Weather(config(), offline=True)
    payload = {"hourly_units": {"wind_speed_10m": "m/s", "wind_speed_100m": "m/s", "wind_direction_100m": "°", "temperature_2m": "°C"},
               "hourly": {"time": ["2026-02-01T00:00"], "wind_speed_10m": [5], "wind_speed_100m": [7], "wind_direction_100m": [90], "temperature_2m": [0]}}
    monkeypatch.setattr(provider, "fetch", lambda *args: {"response": payload})
    with pytest.raises(ValueError, match="complete 48-hour"):
        provider.horizon("2026-02-01T00:00Z")


@pytest.fixture
def run_factory(monkeypatch, tmp_path):
    cfg = config()
    bundle = {"trained_until": "2026-01-01T00:00Z", "version": "test-v1", "selected": "mean", "means": {"turbine_1": 0.3, "turbine_2": 0.4}}
    monkeypatch.setattr("src.pipeline.bundle_for_issue", lambda *_: bundle)
    monkeypatch.setattr("src.pipeline.load_hourly", lambda *_: pd.DataFrame({"valid_time": pd.DatetimeIndex([], tz="UTC"), "complete": pd.Series([], dtype=bool), "turbine_id": [], "power_normalized": []}))

    def weather(issue, run=None):
        issue = utc(issue)
        return pd.concat([pd.DataFrame({"valid_time": pd.date_range(issue, periods=48, freq="h"),
                "issued_at": issue, "turbine_id": turbine, "horizon_hours": range(1, 49),
                "weather_run_time": run, "weather_available_at": run + pd.Timedelta(hours=8),
                "weather_hash": str(run)}) for turbine in cfg["turbines"]], ignore_index=True)

    def factory(issue="2026-02-01T00:00Z"):
        run = ForecastRun(cfg, issue, tmp_path, offline=True)
        monkeypatch.setattr(run.provider, "horizon", weather)
        return run
    return factory


def test_publish_deduplicate_and_revision(run_factory):
    first = run_factory()
    assert run_deterministic(first)["published"]
    validate_forecasts(first.forecast)
    again = run_factory()
    assert run_deterministic(again)["deduplicated"]
    assert again.forecast is None  # No repeated model execution.
    updated = run_factory("2026-02-01T12:00Z")
    assert run_deterministic(updated)["published"]
    assert first.forecast_id != updated.forecast_id
    saved = pd.read_csv(updated.output / "forecasts.csv")
    assert len(saved) == 192
    events = [json.loads(line) for line in (updated.output / "events.jsonl").read_text().splitlines()]
    comparison = [json.loads(e["message"]) for e in events if e["tool"] == "compare_forecasts" and e["status"] == "completed"]
    assert comparison[-1]["overlap_rows"] == 72


def test_guard_order_and_incomplete_output(run_factory):
    run = run_factory()
    with pytest.raises(ValueError, match="Validate inputs"):
        run.call("predict_power")
    with pytest.raises(ValueError, match="Predict and compare"):
        run.call("publish_forecast")
    run_deterministic(run)
    with pytest.raises(ValueError, match="Incomplete"):
        validate_forecasts(run.forecast.iloc[1:])
    with pytest.raises(ValueError, match="both turbines"):
        validate_forecasts(run.forecast.loc[run.forecast.turbine_id.eq("turbine_1")])
    bad = run.forecast.copy()
    bad.loc[0, "power_normalized"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        validate_forecasts(bad)


def test_weather_failure_recovers_with_older_run(run_factory, monkeypatch):
    run = run_factory()
    original = run.provider.horizon
    calls = []

    def fetch(issue, weather_run):
        calls.append(weather_run)
        if len(calls) == 1:
            raise OSError("simulated weather outage")
        return original(issue, weather_run)

    monkeypatch.setattr(run.provider, "horizon", fetch)
    assert run_deterministic(run)["published"]
    assert calls[0] - calls[1] == pd.Timedelta(hours=12)


def test_agent_executes_tools_not_numeric_text(run_factory):
    actions = iter([("fetch_weather", {"older_run": False}), ("prepare_inputs", {}), ("validate_inputs", {}),
                    ("predict_power", {"fallback": False}), ("compare_forecasts", {}), ("publish_forecast", {})])

    def respond(**kwargs):
        name, args = next(actions)
        return SimpleNamespace(output=[SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args), call_id=name)])

    client = SimpleNamespace(responses=SimpleNamespace(create=respond))
    assert run_agent(run_factory(), client=client)["published"]
