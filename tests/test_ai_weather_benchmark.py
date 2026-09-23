"""Leakage, grouping and causal-boundary checks; no model fitting/network."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("catboost")
pytest.importorskip("sklearn")
from src.ai_weather_benchmark import EXTRA_VARIABLES, select_additional, matrix, fit_cutoffs, causal_training
from src.provider_weather import MODELS, VARIABLES

pytestmark = pytest.mark.filterwarnings("ignore::pandas.errors.PerformanceWarning")


def weather_frame():
    issue = pd.Timestamp("2026-01-01T00:00Z")
    w = pd.DataFrame({"issued_at": issue, "valid_time": pd.date_range(issue + pd.Timedelta(hours=24), periods=24, freq="h"),
        "turbine_id": "turbine_1", "horizon_hours": np.arange(25, 49), "wind_speed_10m": 3., "wind_speed_100m": 4.,
        "wind_direction_100m": 90., "temperature_2m": 0., "weather_run_time": issue-pd.Timedelta(hours=12),
        "weather_available_at": issue-pd.Timedelta(hours=4), "provider_offset_hours": [48]*17+[72]*7,
        "provider_available_bound": issue-pd.Timedelta(hours=1), "last_power": .2, "last_wind": 5.,
        "telemetry_age": 2., "power6": .3, "power24": .4, "bias6": .5, "bias24": .7, "bias_count": 6})
    for i, model in enumerate(MODELS):
        for j, variable in enumerate(VARIABLES):
            w[f"provider_{variable}_{model}"] = i + j + np.arange(24)/20
    return w


def archive_frame(w):
    a = w[["valid_time", "turbine_id"]].drop_duplicates().reset_index(drop=True)
    columns = {}
    for i, (model, variables) in enumerate(EXTRA_VARIABLES.items()):
        for j, variable in enumerate(variables):
            for day in [2, 3]:
                columns[f"{variable}_previous_day{day}_{model}"] = day + i + j + np.arange(len(a))/10
    return pd.concat([a, pd.DataFrame(columns)], axis=1)


def test_unavailable_day2_cannot_leak_via_selected_or_trajectory_features():
    w = weather_frame(); a = archive_frame(w)
    selected = select_additional(w, a)
    assert selected.extra_offset_hours.tolist() == [48]*17 + [72]*7
    assert selected.extra_available_bound.le(selected.issued_at).all()
    assert not any("_previous_day" in c for c in selected)
    before = matrix(selected, "add_cat")
    unavailable = a.valid_time > w.issued_at.iloc[0]+pd.Timedelta(hours=40)
    a.loc[unavailable, [c for c in a if "_previous_day2_" in c]] = 99999.
    after = select_additional(w, a)
    pd.testing.assert_frame_equal(selected, after)
    pd.testing.assert_frame_equal(before, matrix(after, "add_cat"))


@pytest.mark.parametrize("method", ["add_cat", "add_hist", "add_cat_live", "add_wind_cat"])
def test_prediction_features_ignore_target_labels_and_future_measured_wind(method):
    w = weather_frame(); selected = select_additional(w, archive_frame(w))
    selected = selected.assign(actual=.3, power_normalized=.3, wind_speed=7., future_actual_wind=8.)
    before = matrix(selected, method)
    selected[["actual", "power_normalized", "wind_speed", "future_actual_wind"]] = 99999.
    pd.testing.assert_frame_equal(before, matrix(selected, method))


def test_forecast_trajectories_do_not_cross_turbines_or_issuances():
    first = weather_frame()
    second = first.assign(turbine_id="turbine_2", wind_speed_100m=999.)
    later = first.copy()
    for col in ["issued_at", "valid_time", "weather_run_time", "weather_available_at", "provider_available_bound"]:
        later[col] += pd.Timedelta(days=2)
    together = pd.concat([first, second, later], ignore_index=True)
    combined = select_additional(together, archive_frame(together))
    before = matrix(combined, "add_cat").iloc[:24].reset_index(drop=True)
    other = combined.index >= 24
    cols = [c for c in combined if "wind_speed" in c or "wind_direction" in c]
    combined.loc[other, cols] = 123456.
    after = matrix(combined, "add_cat").iloc[:24].reset_index(drop=True)
    pd.testing.assert_frame_equal(before, after)


def test_invalid_archive_keys_and_unavailable_offsets_are_rejected():
    w = weather_frame(); a = archive_frame(w)
    with pytest.raises(pd.errors.MergeError):
        select_additional(w, pd.concat([a, a.iloc[:1]], ignore_index=True))
    w.issued_at -= pd.Timedelta(days=3)
    with pytest.raises(ValueError, match="No eligible"):
        select_additional(w, a)


def test_full_january_first_day_uses_earlier_fit_and_labels_must_mature():
    month = pd.Timestamp("2026-01-01T00:00Z")
    targets = pd.DataFrame({"issued_at": [month-pd.Timedelta(days=1), month, month+pd.Timedelta(days=1)]})
    assert fit_cutoffs(targets, month).tolist() == [month-pd.Timedelta(days=1), month, month]
    times = [month-pd.Timedelta(hours=h) for h in [4, 3, 2, 1]] + [month, month+pd.Timedelta(days=1)]
    hourly = pd.DataFrame({"valid_time": times, "turbine_id": "turbine_1", "complete": True,
        "power_normalized": [.1, .2, .3, .4, .5, .6], "wind_speed": [3., 4., 5., 6., 7., 8.]})
    weather = hourly[["valid_time", "turbine_id"]].assign(issued_at=month-pd.Timedelta(days=3), weather_available_at=month-pd.Timedelta(days=4))
    train, obs = causal_training(hourly, weather, month)
    assert train.power_normalized.tolist() == [.1, .2]
    assert obs.power_normalized.tolist() == [.1, .2]
    assert (train.valid_time+pd.Timedelta(hours=3) <= month).all()
    hourly.loc[2:, ["power_normalized", "wind_speed"]] = 99999.
    again, observed = causal_training(hourly, weather, month)
    pd.testing.assert_frame_equal(train, again)
    pd.testing.assert_frame_equal(obs, observed)


def test_matched_no_extra_control_ignores_additional_weather_features():
    w = weather_frame(); selected = select_additional(w, archive_frame(w))
    before = matrix(selected, "no_extra_cat")
    columns = [c for c in selected if c.startswith("extra_") and c != "extra_available_bound"]
    selected[columns] = 99999.
    pd.testing.assert_frame_equal(before, matrix(selected, "no_extra_cat"))
