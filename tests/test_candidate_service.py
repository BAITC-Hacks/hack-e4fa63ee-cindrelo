"""The dashboard service actually serves the portable selected candidate."""
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import agent, common, data, model, pipeline, service, weather
from src.candidate import load_bundle
from ui.data import load_dashboard

REPO = common.ROOT
ISSUE = "2026-01-09T19:00:00Z"


@pytest.fixture
def candidate_root(tmp_path, monkeypatch):
    for module in (agent, common, data, model, pipeline, service, weather):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    for name in ("backend", "dashboard", "candidate"):
        shutil.copytree(REPO / "examples" / name, tmp_path / "examples" / name)
    shutil.copyfile(REPO / "config.json", tmp_path / "config.json")
    (tmp_path / "docs").mkdir()
    for file in (REPO / "docs").glob("*.csv"):
        shutil.copyfile(file, tmp_path / "docs" / file.name)
    return tmp_path


def cycle(root, **kwargs):
    return service.run_forecast_cycle(issued_at=ISSUE, output=root / "outputs/run",
                                    controller="deterministic", offline=True, **kwargs)


def test_selected_candidate_publishes_full_cycle_with_own_metrics(candidate_root, monkeypatch):
    # Legacy artifacts must not override either the selected bundle or its scores.
    artifacts = candidate_root / "artifacts"
    artifacts.mkdir()
    (artifacts / "final.pkl").write_bytes(b"must never be unpickled")
    shutil.copyfile(candidate_root / "examples/dashboard/metrics.csv", artifacts / "metrics.csv")
    calls = []
    predict = pipeline.predict

    def capture(bundle, frame, method):
        calls.append((bundle, frame.copy(), method))
        return predict(bundle, frame, method)

    monkeypatch.setattr(pipeline, "predict", capture)
    first = cycle(candidate_root)
    view = load_dashboard(Path(first["output_dir"]))
    assert first["model"] == "aifs_gem"
    assert len(view.forecasts) == 192 and len(first["forecast_ids"]) == 2
    assert view.forecasts.model_version.str.endswith(":aifs_gem").all()
    assert view.forecasts.power_normalized.between(0, 1).all()
    assert view.metrics.model.eq("aifs_gem").all()
    assert set(view.metrics.horizon_bucket) == {"25-48"}
    assert view.metrics.n_samples.sum() == 1488
    assert np.average(view.metrics.mae, weights=view.metrics.n_samples) == pytest.approx(.16073748807186025)
    assert view.manifest["model_policy"]["horizons_1_24"] == "empirical_curve"
    assert view.manifest["active_method"] == "aifs_gem"
    assert "not independently evaluated" in view.manifest["metrics_scope"]
    assert len(calls) == 2
    for bundle, frame, method in calls:
        assert method == "aifs_gem"
        short = frame.horizon_hours <= 24
        expected = model.predict(bundle, frame.loc[short], "curve")
        actual = model.predict(bundle, frame)[short]
        np.testing.assert_array_equal(actual, expected)
    content = (Path(first["output_dir"]) / "forecasts.csv").read_bytes()
    second = cycle(candidate_root)
    assert all(r["deduplicated"] for r in second["results"])
    assert len(calls) == 2
    assert (Path(second["output_dir"]) / "forecasts.csv").read_bytes() == content
    assert load_dashboard(Path(second["output_dir"])).metrics.equals(view.metrics)


def test_candidate_recovery_is_named_and_failed_revision_keeps_snapshot(candidate_root, monkeypatch):
    predict = pipeline.predict

    def fail_candidate(bundle, frame, method):
        if method == "aifs_gem":
            raise ValueError("Injected model failure")
        return predict(bundle, frame, method)

    monkeypatch.setattr(pipeline, "predict", fail_candidate)
    first = cycle(candidate_root)
    view = load_dashboard(Path(first["output_dir"]))
    assert view.forecasts.model_version.str.endswith(":curve").all()
    assert view.manifest["active_method"] == "curve"
    assert "recovery" in view.manifest["description"]
    assert any("do not score this fallback" in w for w in view.manifest["warnings"])
    pointer = (candidate_root / "outputs/run/current.json").read_bytes()
    original = service.run_deterministic
    count = 0

    def fail_revision(run):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Injected revision failure")
        return original(run)

    monkeypatch.setattr(service, "run_deterministic", fail_revision)
    with pytest.raises(service.ForecastCycleError, match="Previous results were kept"):
        cycle(candidate_root)
    assert (candidate_root / "outputs/run/current.json").read_bytes() == pointer


def test_tampered_portable_candidate_is_not_silently_replaced(candidate_root):
    manifest = json.loads((candidate_root / "examples/candidate/manifest.json").read_text(encoding="utf-8"))
    metadata = candidate_root / "examples/candidate" / manifest["bundles"][-1]["file"]
    metadata.write_bytes(metadata.read_bytes() + b" ")
    with pytest.raises(service.ForecastCycleError, match="Cannot start"):
        cycle(candidate_root)
    assert not (candidate_root / "outputs/run/current.json").exists()


def test_changed_initial_and_reused_revision_keep_selected_provenance(candidate_root, monkeypatch):
    from src.candidate_weather import CandidateWeather
    first = cycle(candidate_root)
    before = load_dashboard(Path(first["output_dir"]))
    horizon = CandidateWeather.horizon
    predict = pipeline.predict

    def changed_initial(provider, issue, run=None):
        frame = horizon(provider, issue, run)
        if issue == common.utc(ISSUE):
            frame["wind_speed_100m"] += .2
            frame["weather_hash"] = common.digest({"original": frame.weather_hash.iloc[0], "injected_change": .2})
            provider.provenance["selected_content_hash"] = frame.weather_hash.iloc[0]
        return frame

    def recover_initial(bundle, frame, method):
        if method == "aifs_gem" and frame.issued_at.iloc[0] == common.utc(ISSUE):
            raise ValueError("Injected initial-only failure")
        return predict(bundle, frame, method)

    monkeypatch.setattr(CandidateWeather, "horizon", changed_initial)
    monkeypatch.setattr(pipeline, "predict", recover_initial)
    second = cycle(candidate_root)
    view = load_dashboard(Path(second["output_dir"]))
    assert second["results"][0]["published"] and second["results"][1]["deduplicated"]
    assert second["forecast_id"] == first["forecast_id"]
    assert view.manifest["active_method"] == "aifs_gem"
    assert view.manifest["weather_provenance"] == before.manifest["weather_provenance"]
    assert view.manifest["forecast_metadata"][second["forecast_ids"][0]]["active_method"] == "curve"
    assert any("recovery versions" in w for w in view.manifest["warnings"])
