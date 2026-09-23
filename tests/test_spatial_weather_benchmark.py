import numpy as np
import pandas as pd
import pytest
pytest.importorskip("sklearn")
from src.spatial_weather_benchmark import POINTS, VARIABLES, MODELS, spatial_features, matrix


def spatial_inputs():
    issue = pd.Timestamp("2026-01-01T00:00Z")
    leads = [24, 40, 41, 47]
    w = pd.DataFrame({"issued_at": issue, "valid_time": issue + pd.to_timedelta(leads, unit="h"),
        "turbine_id": "turbine_1", "horizon_hours": np.array(leads)+1, "wind_speed_10m": 3.,
        "wind_speed_100m": 4., "wind_direction_100m": 90., "temperature_2m": 0.,
        "weather_run_time": issue-pd.Timedelta(hours=12), "actual": .2})
    records = []
    for point in POINTS:
        row, column = int(point[1]), int(point[3])
        p = w[["valid_time"]].assign(point=point)
        for variable in VARIABLES:
            for model in MODELS:
                for offset in [2, 3]:
                    p[f"{variable}_previous_day{offset}_{model}"] = offset*100. + row*2 + column
        records.append(p)
    return w, pd.concat(records, ignore_index=True)


def test_spatial_features_use_only_eligible_offset_and_ignore_target_power():
    w, archive = spatial_inputs()
    selected = spatial_features(w, archive)
    col = "spatial_pressure_msl_gfs_global__r1c1"
    assert selected[col].tolist() == [203., 203., 303., 303.]
    assert not any("_previous_day" in c for c in selected)
    expected = matrix(selected, "spatial_direct")
    for c in archive:
        if "_previous_day2_" in c:
            archive.loc[archive.valid_time >= w.valid_time.iloc[2], c] = 99999.
    w.actual = 10000.
    pd.testing.assert_frame_equal(expected, matrix(spatial_features(w, archive), "spatial_direct"))


def test_pressure_gradient_has_correct_orientation_and_control_excludes_spatial():
    w, archive = spatial_inputs()
    frame = spatial_features(w, archive)
    x = matrix(frame, "gradient_wind")
    east = x["spatial_pressure_msl_gfs_global__gradient_east"]
    north = x["spatial_pressure_msl_gfs_global__gradient_north"]
    assert np.allclose(east, 2/(111.32*.5*np.cos(np.deg2rad(43.644174))))
    assert np.allclose(north, 4/(111.32*.5))
    assert not any(c.startswith("spatial_") for c in matrix(frame, "site_wind"))
    w.issued_at -= pd.Timedelta(hours=48)
    with pytest.raises(ValueError, match="No spatial offset"):
        spatial_features(w, archive)
