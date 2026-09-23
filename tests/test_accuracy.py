import pandas as pd
import pytest

from src.accuracy import observed_before, target_window, training_pairs


def fixture_frames():
    times = pd.to_datetime(["2025-12-31T18:00Z", "2025-12-31T19:00Z", "2026-01-01T19:00Z"])
    hourly = pd.DataFrame(dict(valid_time=times, turbine_id="turbine_1", complete=True,
                               power_normalized=[.1, .2, .3], wind_speed=5.))
    weather = pd.DataFrame(dict(valid_time=times, turbine_id="turbine_1",
                                issued_at=pd.to_datetime(["2025-12-30T19:00Z"] * 2 + ["2025-12-31T19:00Z"]),
                                weather_available_at=pd.to_datetime(["2025-12-30T08:00Z"] * 2 + ["2025-12-31T08:00Z"])))
    return hourly, weather


def test_target_window_retains_previous_month_origin():
    hourly, weather = fixture_frames()
    start = pd.Timestamp("2025-12-31T19:00Z")
    result = target_window(weather, hourly, start, pd.Timestamp("2026-01-31T19:00Z"))
    assert len(result) == 2
    assert result.issued_at.min() < start
    assert result.valid_time.min() == start


def test_training_excludes_unfinished_target():
    hourly, weather = fixture_frames()
    cutoff = pd.Timestamp("2025-12-31T19:00Z")
    assert len(observed_before(hourly, cutoff)) == 1
    assert len(training_pairs(hourly, weather, cutoff)) == 1


def test_evaluation_rejects_future_weather_and_duplicate_keys():
    hourly, weather = fixture_frames()
    start, end = pd.Timestamp("2025-12-01T00:00Z"), pd.Timestamp("2026-02-01T00:00Z")
    bad = weather.copy()
    bad["weather_available_at"] = bad.issued_at + pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match="Unavailable"):
        target_window(bad, hourly, start, end)
    with pytest.raises(ValueError, match="Duplicate"):
        target_window(pd.concat([weather, weather]), hourly, start, end)
