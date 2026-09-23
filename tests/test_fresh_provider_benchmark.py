import numpy as np
import pandas as pd
import pytest
pytest.importorskip("sklearn")
from src.fresh_provider_benchmark import eligible_features, causal_training, matrix
from src.provider_weather import MODELS, VARIABLES


def frames():
    issue = pd.Timestamp("2026-01-01T00:00Z")
    w = pd.DataFrame({"issued_at": issue, "valid_time": pd.date_range(issue + pd.Timedelta(hours=24), periods=24, freq="h"),
                      "turbine_id": "turbine_1", "horizon_hours": np.arange(25, 49), "wind_speed_10m": 3.,
                      "wind_speed_100m": 4., "wind_direction_100m": 90., "temperature_2m": 0.,
                      "weather_run_time": issue - pd.Timedelta(hours=12), "weather_available_at": issue - pd.Timedelta(hours=4)})
    archives = {}
    for offset in [2, 3]:
        p = w[["valid_time", "turbine_id"]].copy()
        for model in MODELS:
            for variable in VARIABLES:
                p[f"{variable}_previous_day{offset}_{model}"] = float(offset)
        archives[offset] = p
    return w, archives


def test_future_offsets_cannot_leak_through_trajectory_features():
    w, archives = frames()
    selected = eligible_features(w, archives)
    assert selected.provider_offset_hours.tolist() == [48] * 17 + [72] * 7
    assert selected.provider_available_bound.le(w.issued_at).all()
    assert not any("_previous_day" in col for col in selected)
    x = matrix(selected, "fresh_cat_time")
    # Poison unavailable day2 values. Neighbouring forecast means/shifts must
    # still be invariant; selecting only at prediction time would leak here.
    for col in archives[2]:
        if "_previous_day2_" in col:
            archives[2].loc[17:, col] = 99999.
    pd.testing.assert_frame_equal(x, matrix(eligible_features(w, archives), "fresh_cat_time"))
    assert "hour_sin" in x
    assert "hour_sin" not in matrix(selected, "fresh_cat")


def test_no_eligible_offset_is_rejected():
    w, archives = frames()
    w.issued_at -= pd.Timedelta(hours=48)
    with pytest.raises(ValueError, match="No eligible"):
        eligible_features(w, archives)


def test_fitting_excludes_unreported_training_labels():
    cutoff = pd.Timestamp("2026-01-01T00:00Z")
    h = pd.DataFrame({"valid_time": pd.date_range(cutoff-pd.Timedelta(hours=4), periods=4, freq="h"),
                      "turbine_id": "turbine_1", "complete": True, "power_normalized": [.1, .2, .3, .4], "wind_speed": 5.})
    w = h[["valid_time", "turbine_id"]].assign(issued_at=cutoff-pd.Timedelta(days=2), weather_available_at=cutoff-pd.Timedelta(days=3))
    train, obs = causal_training(h, w, cutoff)
    assert obs.power_normalized.tolist() == [.1, .2]
    assert train.power_normalized.tolist() == [.1, .2]
    h.loc[2:, "power_normalized"] = 999.
    changed, observed = causal_training(h, w, cutoff)
    pd.testing.assert_frame_equal(train, changed)
    pd.testing.assert_frame_equal(obs, observed)
