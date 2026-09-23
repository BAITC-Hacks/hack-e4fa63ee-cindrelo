import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
from src.model_families import estimator, matrix, blend_analysis


def test_preprocessing_is_fitted_only_on_training_rows():
    x=pd.DataFrame({"wind":[1.,2.,3.,4.]*25,"lag":[np.nan,1.,2.,3.]*25})
    model=estimator("knn").fit(x,np.linspace(0,1,100))
    before=model.named_steps["standardscaler"].mean_.copy()
    result=model.predict(pd.DataFrame({"wind":[1000.],"lag":[np.nan]}))
    assert np.isfinite(result).all()
    np.testing.assert_array_equal(before,model.named_steps["standardscaler"].mean_)


def test_static_matrix_cannot_read_targets():
    issue=pd.Timestamp("2025-12-01T00:00Z")
    frame=pd.DataFrame(dict(turbine_id=["turbine_1","turbine_2"],issued_at=issue,
        valid_time=issue,weather_run_time=issue-pd.Timedelta(hours=12),wind_speed_100m=5.,
        wind_speed_10m=4.,wind_direction_100m=45.,temperature_2m=0.,horizon_hours=1,
        power_normalized=[.1,.9],actual=[.2,.8]))
    before=matrix(frame,False)
    frame[["power_normalized","actual"]]=999.
    pd.testing.assert_frame_equal(before,matrix(frame,False))


def test_blend_aligns_on_forecast_keys(tmp_path):
    rows=[]
    for fold,date in [("2025-12","2025-12-20T00:00Z"),("2026-01","2026-01-31T00:00Z")]:
        for turbine in ["turbine_1","turbine_2"]:
            for name,pred in [("curve",.2),("hist_mae_weather",.4)]:
                rows.append(dict(fold=fold,valid_time=date,issued_at=pd.Timestamp(date)-pd.Timedelta(days=1),
                                 turbine_id=turbine,horizon_hours=25,model=name,prediction=pred,actual=.3,error=pred-.3))
    source=tmp_path/"source";source.mkdir()
    pd.DataFrame(rows).sample(frac=1,random_state=42).to_csv(source/"predictions.csv",index=False)
    blend_analysis(source,tmp_path/"result")
    out=pd.read_csv(tmp_path/"result/predictions.csv")
    mixed=out[out.model.eq("blend_hist_mae_weather")]
    assert len(mixed)==4
    np.testing.assert_allclose(mixed.prediction,.3)
    np.testing.assert_allclose(mixed.error,0,atol=1e-12)
