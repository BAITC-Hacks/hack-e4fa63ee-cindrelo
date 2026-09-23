from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.run_bias_benchmark import same_run_state, predictions, evaluate


def sample():
    issue = pd.Timestamp("2026-01-01T00:00Z")
    run = issue-pd.Timedelta(hours=24)
    times = pd.date_range(issue-pd.Timedelta(hours=24), periods=80, freq="h")
    hourly = pd.DataFrame({"valid_time": times, "turbine_id": "turbine_1", "wind_speed": 6.,
                           "power_normalized": .5, "complete": True})
    weather = pd.DataFrame({"issued_at": [issue], "turbine_id": ["turbine_1"],
                            "weather_run_time": [run], "weather_available_at": [run+pd.Timedelta(hours=8)]})
    envelope = {"run_time": run.isoformat(), "content_hash": "test-hash", "response": {
        "hourly_units": {"wind_speed_100m": "m/s"},
        "hourly": {"time": times.astype(str).tolist(), "wind_speed_100m": [4.]*len(times)}}}
    source = Mock()
    source.fetch.return_value = envelope
    return hourly, weather, source


def test_same_run_bias_ignores_unreported_observations_and_counts_exact_windows():
    hourly, weather, source = sample()
    state = same_run_state(hourly, weather, source)
    assert state.bias12.iloc[0] == state.bias18.iloc[0] == 2.
    assert state.pairs12.iloc[0] == 12
    assert state.pairs18.iloc[0] == 18
    issue = weather.issued_at.iloc[0]
    hourly.loc[hourly.valid_time+pd.Timedelta(hours=3) > issue, "wind_speed"] = 10000.
    pd.testing.assert_frame_equal(state, same_run_state(hourly, weather, source))
    source.fetch.assert_called_with("turbine_1", weather.weather_run_time.iloc[0])


def test_stale_history_is_neutral_and_future_or_wrong_run_is_rejected():
    hourly, weather, source = sample()
    issue = weather.issued_at.iloc[0]
    hourly = hourly[hourly.valid_time <= issue-pd.Timedelta(hours=7)]
    state = same_run_state(hourly, weather, source)
    assert state.fallback12.iloc[0] and state.bias12.iloc[0] == 0
    weather.weather_available_at = issue+pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="not available"):
        same_run_state(hourly, weather, source)
    weather.weather_available_at = issue-pd.Timedelta(hours=1)
    source.fetch.return_value["run_time"] = issue.isoformat()
    with pytest.raises(ValueError, match="does not match"):
        same_run_state(hourly, weather, source)


def test_zero_bias_recovers_baseline_and_correction_is_bounded():
    curve = {"turbine_1": (np.array([0., 10.]), np.array([0., 1.]))}
    test = pd.DataFrame({"turbine_id": ["turbine_1"]*2, "wind_speed_100m": [4., 4.],
                         "horizon_hours": [25, 48], "bias12": [0., 0.], "bias18": [0., 0.]})
    p = predictions(curve, test)
    for value in p.values():
        np.testing.assert_allclose(value, p["curve"])
    test.bias12 = 10000.
    changed = predictions(curve, test)["run_bias12_1.0"]
    np.testing.assert_allclose(changed, (4+4*np.exp(-np.array([24, 47])/48))/10)


def test_month_boundary_fits_precede_origins_and_do_not_fit_january_targets():
    first = pd.Timestamp("2026-01-01", tz="Asia/Almaty").tz_convert("UTC")
    times = pd.date_range(first-pd.Timedelta(days=4), periods=6*24, freq="h")
    hourly = pd.DataFrame({"valid_time": times, "turbine_id": "turbine_1", "complete": True,
        "wind_speed": np.tile([4., 6.], len(times)//2), "power_normalized": np.tile([.4, .6], len(times)//2)})
    parts = []
    for issue in [first-pd.Timedelta(days=1), first]:
        parts.append(pd.DataFrame({"issued_at": issue, "valid_time": pd.date_range(issue+pd.Timedelta(days=1), periods=24, freq="h"),
            "turbine_id": "turbine_1", "horizon_hours": np.arange(25, 49), "weather_run_time": issue-pd.Timedelta(days=1),
            "weather_available_at": issue-pd.Timedelta(hours=16), "wind_speed_100m": 4.}))
    weather = pd.concat(parts, ignore_index=True)
    source = Mock()
    def fetch(turbine, run):
        forecast_times = pd.date_range(run, periods=80, freq="h")
        return {"run_time": run.isoformat(), "content_hash": "test", "response": {
            "hourly_units": {"wind_speed_100m": "m/s"}, "hourly": {
                "time": forecast_times.astype(str).tolist(), "wind_speed_100m": [4.]*len(forecast_times)}}}
    source.fetch.side_effect = fetch
    result, _ = evaluate(hourly, weather, source, "2026-01-01", "2026-01-03")
    baseline = result[result.model == "curve"]
    assert len(baseline) == 48
    assert set(baseline.fit_cutoff) == {first-pd.Timedelta(days=1), first}
    assert baseline.fit_cutoff.le(baseline.issued_at).all()
    # Both origins precede all January observations, so poisoning January must
    # change scoring truth but no fitted curve, live feature, or prediction.
    hourly.loc[hourly.valid_time >= first, ["wind_speed", "power_normalized"]] = 999.
    changed, _ = evaluate(hourly, weather, source, "2026-01-01", "2026-01-03")
    np.testing.assert_allclose(result.prediction, changed.prediction)
