import numpy as np
import pandas as pd
from src.live_bias import telemetry


def test_telemetry_respects_reporting_delay_and_exact_six_hour_window():
    issue=pd.Timestamp("2026-01-30T19:00Z")
    times=pd.date_range(issue-pd.Timedelta(hours=12), periods=14, freq="h")
    h=pd.DataFrame(dict(valid_time=times,turbine_id="turbine_1",complete=True,
                        wind_speed=np.arange(14,dtype=float),power_normalized=np.arange(14,dtype=float)))
    w=h[["valid_time","turbine_id"]].copy()
    w["issued_at"]=issue-pd.Timedelta(days=1)
    w["weather_available_at"]=w.issued_at
    w["wind_speed_100m"]=0.
    s=telemetry(h,w,[issue]).set_index("turbine_id").loc["turbine_1"]
    assert s.last_power==9.
    assert s.telemetry_age==2.
    assert s.power6==6.5  # Indices 4..9; each measurement is fully matured.
    h.loc[h.valid_time>issue-pd.Timedelta(hours=3),["wind_speed","power_normalized"]]=999.
    later=telemetry(h,w,[issue]).set_index("turbine_id").loc["turbine_1"]
    pd.testing.assert_series_equal(s,later)


def test_stale_telemetry_has_neutral_correction():
    issue=pd.Timestamp("2026-01-30T19:00Z")
    h=pd.DataFrame(dict(valid_time=[issue-pd.Timedelta(hours=12)],turbine_id="turbine_1",complete=True,wind_speed=20.,power_normalized=1.))
    w=pd.DataFrame(dict(valid_time=h.valid_time,turbine_id="turbine_1",issued_at=issue-pd.Timedelta(days=1),weather_available_at=issue-pd.Timedelta(days=1),wind_speed_100m=1.))
    s=telemetry(h,w,[issue]).set_index("turbine_id").loc["turbine_1"]
    assert s.bias6==0 and s.bias24==0
    assert np.isnan(s.last_power)
