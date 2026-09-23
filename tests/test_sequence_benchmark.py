import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
from src.sequence_benchmark import history_features, sequences, eligible_training, weather_features


def observations():
    times = pd.date_range("2025-11-01", periods=300, freq="h", tz="UTC")
    return pd.DataFrame({"valid_time": times, "turbine_id": "turbine_1", "complete": True,
                         "power_normalized": np.arange(300) / 300, "wind_speed": np.arange(300) / 20})


def test_history_excludes_unavailable_observations_and_never_backfills():
    h = observations()
    issue = h.valid_time.iloc[200]
    initial = history_features(h, [issue])
    assert initial.power_normalized_lag0.iloc[0] == h.power_normalized.iloc[197]
    assert initial.power_normalized_lag167.iloc[0] == h.power_normalized.iloc[30]
    h.loc[h.valid_time + pd.Timedelta(hours=3) > issue, ["power_normalized", "wind_speed"]] = 10000.
    pd.testing.assert_frame_equal(initial, history_features(h, [issue]))
    h.loc[h.valid_time.eq(issue-pd.Timedelta(hours=3)), "complete"] = False
    result = history_features(h, [issue])
    assert np.isnan(result.power_normalized_lag0.iloc[0])
    assert result.observed_history_hours.iloc[0] == 167


def test_training_targets_end_and_mature_before_cutoff():
    h = observations()
    issues = pd.DatetimeIndex([h.valid_time.iloc[100], h.valid_time.iloc[124]])
    y = sequences(h, issues)
    cutoff = issues[0] + pd.Timedelta(hours=50)
    permitted = eligible_training(y, cutoff)
    assert len(permitted) == 1
    assert permitted.index[0][0] == issues[0]
    assert permitted.target_25.iloc[0] == h.power_normalized.iloc[124]
    assert permitted.target_48.iloc[0] == h.power_normalized.iloc[147]
    assert eligible_training(y, cutoff-pd.Timedelta(seconds=1)).empty


def test_weather_features_reject_late_weather_and_ignore_observed_targets():
    issue = pd.Timestamp("2025-12-01T00:00Z")
    w = pd.DataFrame({"issued_at": issue, "turbine_id": "turbine_1", "weather_available_at": issue,
        "horizon_hours": np.arange(1, 49), "valid_time": pd.date_range(issue, periods=48, freq="h"),
        "wind_speed_100m": 5., "wind_speed_10m": 4., "temperature_2m": 0., "wind_direction_100m": 45.,
        "actual": .2})
    first = weather_features(w)
    w["actual"] = 999.
    pd.testing.assert_frame_equal(first, weather_features(w))
    w.loc[0, "weather_available_at"] = issue + pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="unavailable"):
        weather_features(w)
