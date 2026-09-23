import io
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src import agent, common, data, model, pipeline, service, weather
from ui.data import load_dashboard

REPO = common.ROOT
ISSUE = "2026-01-09T19:00:00Z"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for module in (agent, common, data, model, pipeline, service, weather):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    shutil.copytree(REPO / "examples/backend", tmp_path / "examples/backend")
    shutil.copytree(REPO / "examples/dashboard", tmp_path / "examples/dashboard")
    shutil.copyfile(REPO / "config.json", tmp_path / "config.json")
    (tmp_path / "docs").mkdir()
    for file in (REPO / "docs").glob("*.csv"):
        shutil.copyfile(file, tmp_path / "docs" / file.name)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return tmp_path


def run_offline(root, **kwargs):
    return service.run_forecast_cycle(issued_at=ISSUE, output=root / "outputs/run", controller="deterministic", offline=True, **kwargs)


def test_fresh_clone_prepares_revises_and_deduplicates(isolated):
    events = []
    result = run_offline(isolated, on_event=events.append)
    view = load_dashboard(Path(result["output_dir"]))
    assert len(view.forecasts) == 192 and len(result["forecast_ids"]) == 2
    assert result["forecast_id"] in set(view.forecasts.forecast_id)
    assert not view.metrics.empty
    assert sum(e["tool"] == "prepare_inputs" and e["status"] == "completed" for e in events) == 2
    revisions = [json.loads(e["message"]) for e in events if e["tool"] == "compare_forecasts" and e["status"] == "completed"]
    assert revisions[-1]["overlap_rows"] == 72
    file = Path(result["output_dir"]) / "forecasts.csv"
    before = file.read_bytes()
    second = run_offline(isolated)
    assert all(item["deduplicated"] for item in second["results"])
    assert file.read_bytes() == before
    assert (Path(second["output_dir"]) / "forecasts.csv").read_bytes() == before
    # The updated raw source is prepared again on a later click, not silently
    # ignored because the configuration hash stayed the same.
    source = next((isolated / "docs").glob("*turbine 1.csv"))
    raw = pd.read_csv(source)
    timestamps = pd.to_datetime(raw.iloc[:, 1], format="mixed")
    raw.loc[timestamps.between("2026-01-10 12:00", "2026-01-10 12:50"), raw.columns[3]] = 0.123
    raw.to_csv(source, index=False, encoding="utf-8")
    updated = run_offline(isolated, include_update=False)
    actuals = load_dashboard(Path(updated["output_dir"])).actuals
    row = actuals.loc[actuals.turbine_id.eq("turbine_1") & actuals.valid_time.eq(common.utc("2026-01-10T07:00Z"))]
    assert row.power_normalized.iloc[0] == pytest.approx(0.123)
    assert (Path(updated["output_dir"]) / "forecasts.csv").read_bytes() == before


def test_failures_keep_last_snapshot_and_save_trace(isolated, monkeypatch):
    first = run_offline(isolated)
    output = Path(first["output_dir"])
    before = {name: (output / name).read_bytes() for name in service.FILES}
    original = service.run_deterministic
    calls = []

    def fail_revision(run):
        calls.append(run)
        if len(calls) == 2:
            run.event("injected_failure", "failed", "Failure during updated-weather replay")
            run.flush_events()
            raise RuntimeError("private-debug-data")
        return original(run)

    monkeypatch.setattr(service, "run_deterministic", fail_revision)
    with pytest.raises(service.ForecastCycleError, match="Previous results were kept") as error:
        run_offline(isolated)
    assert "private-debug-data" not in str(error.value)
    assert before == {name: (output / name).read_bytes() for name in service.FILES}
    assert list((output.parent.parent / "failed-cycles").glob("*/events.jsonl"))


def test_snapshot_pointer_failure_preserves_previous_view(isolated, monkeypatch):
    first = run_offline(isolated)
    old = Path(first["output_dir"])
    before = {name: (old / name).read_bytes() for name in service.FILES}
    pointer = isolated / "outputs/run/current.json"
    old_pointer = pointer.read_bytes()
    original = service.write_json

    def fail_pointer(path, value):
        if Path(path).name == "current.json":
            raise PermissionError("Injected publication failure")
        return original(path, value)

    monkeypatch.setattr(service, "write_json", fail_pointer)
    with pytest.raises(service.ForecastCycleError, match="Previous results were kept"):
        run_offline(isolated)
    assert pointer.read_bytes() == old_pointer
    assert before == {name: (old / name).read_bytes() for name in service.FILES}


def test_key_path_time_and_concurrent_run_guards(isolated):
    with pytest.raises(service.ForecastCycleError, match="OPENAI_API_KEY"):
        service.run_forecast_cycle(issued_at=ISSUE, output=isolated / "outputs/run")
    with pytest.raises(service.ForecastCycleError, match="under outputs"):
        service.run_forecast_cycle(issued_at=ISSUE, output=isolated / "examples/dashboard", controller="deterministic")
    with pytest.raises(service.ForecastCycleError, match="hourly UTC"):
        service.run_forecast_cycle(issued_at="2026-01-09T19:30Z", output=isolated / "outputs/run", controller="deterministic")
    with pytest.raises(service.ForecastCycleError, match="predates"):
        service.run_forecast_cycle(issued_at="2025-01-01T00:00Z", output=isolated / "outputs/run", controller="deterministic")
    with service._CYCLE_LOCK:
        with pytest.raises(service.ForecastCycleError, match="Another forecast cycle"):
            run_offline(isolated)


def test_refresh_hash_tracks_weather_values_not_provider_timing(isolated, monkeypatch):
    cfg = common.config()
    bundled = isolated / "examples/backend/weather"
    cached = weather.Weather(cfg, offline=True, cache_dir=bundled, stable_hashes=True)
    before = cached.horizon(ISSUE)
    responses = []
    for turbine in cfg["turbines"]:
        envelope = cached.fetch(turbine, weather.eligible_run(ISSUE, 8))
        response = envelope["response"]
        response["generationtime_ms"] = 999.0
        responses.append(response)
    queue = iter(responses)
    monkeypatch.setattr(weather, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(next(queue)).encode("utf-8")))
    monkeypatch.setattr(weather.time, "sleep", lambda _: None)
    online = weather.Weather(cfg, refresh=True, stable_hashes=True, attempts=1)
    refreshed = online.horizon(ISSUE)
    assert refreshed.weather_hash.iloc[0] == before.weather_hash.iloc[0]
    assert {x["source"] for x in online.retrievals} == {"external"}
    responses[0]["hourly"]["wind_speed_100m"][19] += 1.0
    queue = iter(responses)
    changed = online.horizon(ISSUE)
    assert changed.weather_hash.iloc[0] != before.weather_hash.iloc[0]


def test_online_outage_uses_verified_cache_with_explicit_source(isolated, monkeypatch):
    def outage(*args, **kwargs):
        raise OSError("Provider unavailable")

    monkeypatch.setattr(weather, "urlopen", outage)
    cfg = common.config()
    provider = weather.Weather(cfg, refresh=True, stable_hashes=True, attempts=1,
                               fallback_cache_dirs=(isolated / "examples/backend/weather",))
    assert len(provider.horizon(ISSUE)) == 96
    assert all(x["source"] == "cached_fallback" for x in provider.retrievals)
    # Tampering still fails before cached weather can be used as recovery.
    file = next((isolated / "examples/backend/weather").glob("*.json"))
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["content_hash"] = "invalid"
    common.write_json(file, payload)
    run = common.utc(payload["run_time"])
    turbine = next(t for t, point in cfg["turbines"].items() if point["latitude"] == payload["request"]["latitude"])
    with pytest.raises(ValueError, match="checksum mismatch"):
        provider.fetch(turbine, run)
