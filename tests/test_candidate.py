"""Portable candidate equivalence, hybrid boundaries and archive safeguards."""
import copy
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("catboost")
from src import candidate as c
from src.candidate_weather import CandidateWeather, validated_previous_runs
from src.common import ROOT, config, digest, utc

ISSUE = utc("2026-01-09T19:00:00Z")
pytestmark = pytest.mark.filterwarnings("ignore::pandas.errors.PerformanceWarning")


def synthetic():
    frames = []
    for turbine in ["turbine_1", "turbine_2"]:
        frame = pd.DataFrame({"issued_at": ISSUE, "valid_time": pd.date_range(ISSUE, periods=48, freq="h"),
            "turbine_id": turbine, "horizon_hours": np.arange(1, 49), "wind_speed_10m": 3.,
            "wind_speed_100m": np.linspace(3, 8, 48), "temperature_2m": 1., "wind_direction_100m": 90.,
            "weather_run_time": ISSUE-pd.Timedelta(hours=19), "weather_available_at": ISSUE-pd.Timedelta(hours=11),
            "provider_offset_hours": [48]*41+[72]*7, "provider_available_bound": ISSUE-pd.Timedelta(hours=1)})
        for i, model in enumerate(c.MODELS):
            for j, variable in enumerate(c.VARIABLES):
                frame[f"provider_{variable}_{model}"] = i+j+np.arange(48)/20
        frame["provider_wind_speed_100m_jma_gsm"] = np.nan
        frame["extra_offset_hours"] = frame.provider_offset_hours
        frame["extra_available_bound"] = frame.provider_available_bound
        for i, (model, variables) in enumerate(c.EXTRA_VARIABLES.items()):
            for j, variable in enumerate(variables):
                frame[f"extra_{variable}_{model}"] = i+j+np.arange(48)/15
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def test_runtime_matrix_and_explicit_imputation_equal_research():
    pytest.importorskip("sklearn")
    from sklearn.impute import SimpleImputer
    from src.ai_weather_benchmark import matrix as research_matrix
    day = synthetic().query("horizon_hours > 24").reset_index(drop=True)
    pd.testing.assert_frame_equal(c.matrix(day), research_matrix(day, "add_wind_cat"))
    x = c.matrix(day)
    usable = x.columns[~x.isna().all()]
    x.loc[0, usable[1]] = np.nan
    # Modify a raw feature so both the source and derived missing values match.
    day.loc[0, "wind_speed_10m"] = np.nan
    x = c.matrix(day)[usable]
    imputer = SimpleImputer(strategy="median", add_indicator=True).fit(x)
    metadata = {"features": list(usable), "medians": imputer.statistics_.tolist(),
                "indicator_indices": imputer.indicator_.features_.tolist()}
    np.testing.assert_array_equal(c.transform(day, metadata), imputer.transform(x))


def test_hybrid_short_head_cannot_change_learned_trajectory_or_use_actuals():
    frame = synthetic()
    bundle = c.load_bundle(config(), ISSUE, ROOT / "examples/candidate")
    before = c.predict(frame, bundle)
    day = frame.horizon_hours > 24
    np.testing.assert_array_equal(before[~day], np.clip(c.curve_predict(frame.loc[~day], bundle["curves"]), 0, 1))
    np.testing.assert_array_equal(before[day], c.predict(frame.loc[day], bundle))
    frame.loc[~day, "wind_speed_100m"] = 99.
    frame["actual"], frame["wind_speed"], frame["power_normalized"] = 999., 999., 999.
    np.testing.assert_array_equal(before[day], c.predict(frame, bundle)[day])
    with pytest.raises(ValueError, match="complete horizons"):
        c.predict(frame.drop(index=47), bundle)
    frame.loc[day, "extra_available_bound"] = ISSUE+pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="unavailable"):
        c.predict(frame, bundle)


def test_native_model_error_is_normalized_for_controller_recovery():
    bundle = c.load_bundle(config(), ISSUE, ROOT / "examples/candidate")
    with patch.object(bundle["model"], "predict", side_effect=c.CatBoostError("native failure")):
        with pytest.raises(RuntimeError, match="empirical-curve recovery"):
            c.predict(synthetic(), bundle)


def test_portable_bundle_boundary_checksums_and_metrics(tmp_path):
    cfg = config()
    directory = ROOT / "examples/candidate"
    early = c.load_bundle(cfg, utc("2025-12-30T19:00Z"), directory)
    main = c.load_bundle(cfg, ISSUE, directory)
    assert early["trained_until"] == "2025-12-30T19:00:00Z"
    assert main["trained_until"] == "2025-12-31T19:00:00Z"
    assert utc(main["last_training_target_available_at"]) <= utc(main["trained_until"])
    assert len(main["metrics"]) == 2
    assert {r["turbine_id"] for r in main["metrics"]} == set(cfg["turbines"])
    assert all(r["horizon_bucket"] == "25-48" and r["model"] == "aifs_gem" for r in main["metrics"])
    with pytest.raises(ValueError, match="No candidate"):
        c.load_bundle(cfg, utc("2025-12-30T18:00Z"), directory)
    copied = tmp_path / "candidate"
    shutil.copytree(directory, copied)
    path = copied / "january.cbm"
    path.write_bytes(path.read_bytes()+b"corruption")
    with pytest.raises(ValueError, match="model checksum"):
        c.load_bundle(cfg, ISSUE, copied)


def test_candidate_runtime_works_without_optional_research_dependencies():
    script = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'sklearn' or fullname.startswith('sklearn.') or fullname in {'src.ai_weather_benchmark', 'src.fresh_provider_benchmark', 'src.accuracy'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Block())
from src.candidate import load_bundle
from src.common import ROOT, config
b = load_bundle(config(), '2026-01-09T19:00Z', ROOT / 'examples/candidate')
assert b['selected'] == 'aifs_gem'
"""
    completed = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_portable_metadata_loads_with_lf_and_crlf_without_changing_model_bytes(tmp_path):
    directory = tmp_path / "portable"
    shutil.copytree(ROOT / "examples/candidate", directory)
    original = c.load_bundle(config(), ISSUE, directory)
    expected = c.predict(synthetic(), original)
    model_hash = c.sha256(directory / "january.cbm")
    for newline in [b"\n", b"\r\n"]:
        for path in directory.glob("*.json"):
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
        loaded = c.load_bundle(config(), ISSUE, directory)
        assert loaded["version"] == original["version"]
        assert c.sha256(directory / "january.cbm") == model_hash
        np.testing.assert_array_equal(c.predict(synthetic(), loaded), expected)


@pytest.mark.parametrize("issue", [ISSUE, ISSUE+pd.Timedelta(hours=12)])
def test_bundled_weather_serves_both_demo_issuances_offline(tmp_path, issue):
    provider = CandidateWeather(config(), offline=True, cache_dir=tmp_path)
    frame = provider.horizon(issue)
    assert len(frame) == 96 and frame.weather_hash.nunique() == 1
    assert frame.provider_available_bound.le(frame.issued_at).all()
    assert frame.extra_available_bound.le(frame.issued_at).all()
    assert not any("_previous_day" in column for column in frame)
    assert all(sorted(part.horizon_hours) == list(range(1, 49)) for _, part in frame.groupby("turbine_id"))
    prediction = c.predict(frame, c.load_bundle(config(), issue, ROOT / "examples/candidate"))
    assert np.isfinite(prediction).all()
    if issue == ISSUE:
        reference = json.loads((ROOT / "examples/candidate/demo_reference.json").read_text(encoding="utf-8"))
        day = frame.assign(prediction=prediction).query("horizon_hours > 24").sort_values(["turbine_id", "valid_time"])
        np.testing.assert_allclose(day.prediction, [r["prediction"] for r in reference["predictions"]], atol=1e-12, rtol=0)
    assert not list(tmp_path.iterdir())  # Immutable bundled cache is sufficient.


def test_candidate_weather_hash_covers_selected_inputs_only(tmp_path):
    provider = CandidateWeather(config(), offline=True, cache_dir=tmp_path)
    before = provider.horizon(ISSUE)
    original = provider.fetch_group

    def changed(group, issue):
        record, _, _ = original(group, issue)
        record = copy.deepcopy(record)
        if group == "extra":
            for payload in record["response"]:
                hourly = payload["hourly"]
                for i, timestamp in enumerate(pd.to_datetime(hourly["time"], utc=True)):
                    if timestamp > ISSUE+pd.Timedelta(hours=40):
                        hourly["wind_speed_100m_previous_day2_ecmwf_aifs025_single"][i] = 99999.
        selected, grids = validated_previous_runs(record["response"], config(), issue, group)
        return record, selected, grids

    with patch.object(provider, "fetch_group", side_effect=changed):
        poisoned_unused = provider.horizon(ISSUE)
    pd.testing.assert_frame_equal(before, poisoned_unused)

    def changed_used(group, issue):
        record, selected, grids = original(group, issue)
        if group == "extra":
            selected = selected.copy()
            selected.loc[0, "extra_wind_speed_100m_ecmwf_aifs025_single"] += .1
        return record, selected, grids

    with patch.object(provider, "fetch_group", side_effect=changed_used):
        assert provider.horizon(ISSUE).weather_hash.iloc[0] != before.weather_hash.iloc[0]


def test_malformed_refresh_preserves_last_valid_supplemental_cache(tmp_path):
    provider = CandidateWeather(config(), offline=True, cache_dir=tmp_path)
    record, expected, _ = provider.fetch_group("extra", ISSUE)
    target = tmp_path / (digest(provider.request_params("extra", ISSUE))+".json")
    target.write_text(json.dumps(record), encoding="utf-8")
    original_bytes = target.read_bytes()
    provider.offline, provider.refresh = False, True
    malformed = copy.deepcopy(record["response"])
    malformed[0]["hourly"]["time"] = []
    with patch("src.candidate_weather.urlopen", return_value=io.BytesIO(json.dumps(malformed).encode())):
        _, selected, _ = provider.fetch_group("extra", ISSUE)
    pd.testing.assert_frame_equal(expected, selected)
    assert target.read_bytes() == original_bytes
    assert provider.retrievals[-1]["source"] == "cached_fallback"
