import pandas as pd
import pytest
pytest.importorskip("sklearn")
from src.provider_benchmark import matrix


def test_provider_features_are_filtered_and_exclude_actuals():
    t=pd.Timestamp("2026-01-01T00:00Z")
    p=pd.DataFrame(dict(turbine_id=["turbine_1"],issued_at=t,valid_time=t,weather_run_time=t-pd.Timedelta(hours=12),
        wind_speed_10m=3.,wind_speed_100m=5.,wind_direction_100m=90.,temperature_2m=2.,horizon_hours=1,
        wind_speed_10m_previous_day3_gfs_global=4.,wind_speed_10m_previous_day3_jma_gsm=6.,actual=.7))
    x=matrix(p,"gfs_cat")
    assert "wind_speed_10m_previous_day3_gfs_global" in x
    assert "wind_speed_10m_previous_day3_jma_gsm" not in x
    assert "actual" not in x
    p.actual=999.
    pd.testing.assert_frame_equal(x,matrix(p,"gfs_cat"))
