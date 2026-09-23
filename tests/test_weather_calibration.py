import pandas as pd
import numpy as np

from src.weather_calibration import inputs


def test_trajectory_features_never_cross_issuance_or_read_actuals():
    frames = []
    for issue, speeds in [("2025-12-01T00:00Z", [1., 2., 3.]), ("2025-12-02T00:00Z", [90., 91., 92.])]:
        t = pd.Timestamp(issue)
        frames.append(pd.DataFrame(dict(turbine_id="turbine_1", issued_at=t,
                        valid_time=pd.date_range(t, periods=3, freq="h"), weather_run_time=t-pd.Timedelta(hours=12),
                        wind_speed_100m=speeds, wind_speed_10m=speeds, temperature_2m=1., wind_direction_100m=90.,
                        horizon_hours=[1,2,3], power_normalized=[.1,.2,.3])))
    frame = pd.concat(frames, ignore_index=True)
    before = inputs(frame)
    frame.power_normalized = 999.
    pd.testing.assert_frame_equal(inputs(frame), before)
    assert before.loc[2,"wind_shift_-1"] == 3.
    assert before.loc[3,"wind_shift_1"] == 90.
    np.testing.assert_allclose(before.wind_u,0,atol=1e-10)
