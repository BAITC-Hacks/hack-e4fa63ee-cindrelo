"""December-selected benchmark using fixed-lead forecasts from other providers."""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesRegressor
from threadpoolctl import threadpool_limits
from .accuracy import curves,curve_predict,observed_before,training_pairs,target_window,summarize
from .common import config,local_date,write_csv,write_json
from .data import load_hourly
from .live_bias import telemetry
from .weather_calibration import weather_table,inputs

METHODS=["curve","multi_hist","multi_cat","multi_extra","multi_cat_live","multi_hist_live","gfs_cat","icon_cat","jma_cat","multi_wind"]


def matrix(frame,method):
    x=inputs(frame,physical=True)
    x["turbine_id"]=frame.turbine_id.map({"turbine_1":0.,"turbine_2":1.})
    provider={"gfs_cat":"gfs_global","icon_cat":"icon_global","jma_cat":"jma_gsm"}.get(method)
    for col in frame.columns:
        if "_previous_day3_" in col and (provider is None or col.endswith(provider)):
            if "wind_direction" in col:
                x[col+"_sin"]=np.sin(np.deg2rad(frame[col]));x[col+"_cos"]=np.cos(np.deg2rad(frame[col]))
            else:x[col]=frame[col]
    if method.endswith("live"):
        for col in ["last_power","last_wind","telemetry_age","power6","power24","bias6","bias24","bias_count"]:x[col]=frame[col]
    return x


def fit_predict(method,train,obs,test):
    base=curves(obs);baseline=curve_predict(base,test)
    if method=="curve":return baseline
    if "hist" in method:
        model=HistGradientBoostingRegressor(loss="absolute_error",max_iter=300,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=10,early_stopping=False,learning_rate=.05,random_state=42)
    elif "extra" in method:
        model=ExtraTreesRegressor(n_estimators=150,max_depth=18,min_samples_leaf=15,n_jobs=4,random_state=42)
    else:
        model=CatBoostRegressor(iterations=350,depth=5,learning_rate=.03,l2_leaf_reg=10,loss_function="MAE",random_seed=42,thread_count=4,verbose=False,allow_writing_files=False)
    x,y=matrix(train,method),matrix(test,method)
    usable=x.columns[~x.isna().all()]
    pipeline=make_pipeline(SimpleImputer(strategy="median",add_indicator=True),model)
    with threadpool_limits(limits=4):
        pipeline.fit(x[usable],train.wind_speed if method=="multi_wind" else train.power_normalized)
        p=pipeline.predict(y[usable])
    if method=="multi_wind":p=curve_predict(base,test,np.maximum(0,p))
    if method.endswith("live"):
        stale=test.telemetry_age.gt(5).to_numpy();p[stale]=baseline[stale]
    return np.clip(p,0,1)


def evaluate(h,w,state,provider,start,end,methods):
    cutoff=local_date(start,config());obs=observed_before(h,cutoff)
    train=training_pairs(h,w,cutoff).merge(provider,on=["valid_time","turbine_id"],validate="many_to_one").merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    test=target_window(w,h,cutoff,local_date(end,config()))
    test=test[test.issued_at>=cutoff].merge(provider,on=["valid_time","turbine_id"],validate="many_to_one").merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    rows=[]
    for method in methods:
        part=test.copy();part["prediction"]=fit_predict(method,train,obs,test)
        if not np.isfinite(part.prediction).all():raise ValueError("Nonfinite prediction")
        part["error"]=part.prediction-part.actual;part["model"]=method;part["fold"]=start[:7];rows.append(part)
        print(start,method,part[part.horizon_hours>24].error.abs().mean(),flush=True)
    return pd.concat(rows,ignore_index=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default="outputs/provider-benchmark");args=parser.parse_args()
    out=Path(args.output)
    if out.exists():raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    h,w=load_hourly(config()),weather_table();state=telemetry(h,w,w.issued_at.unique())
    provider=pd.read_csv("outputs/provider-weather/weather.csv");provider.valid_time=pd.to_datetime(provider.valid_time,utc=True)
    write_json(out/"protocol.json",{"methods":METHODS,"selection":"December day-ahead MAE","january":"reused diagnostic; winner frozen before evaluation",
        "target_mae_max":.05,"provider_offset_hours":72,"exact_publication_unverified":True,"training":"June-November for December; June-December for January"})
    dec=evaluate(h,w,state,provider,"2025-12-01","2026-01-01",METHODS)
    scores=dec[dec.horizon_hours>24].groupby("model").error.apply(lambda x:x.abs().mean()).sort_values()
    selected=scores.index[0];write_json(out/"selection.json",{"selected":selected,"december_mae":scores.to_dict()})
    write_csv(out/"december_predictions.csv",dec)
    jan=evaluate(h,w,state,provider,"2026-01-01","2026-02-01",METHODS)
    allp=pd.concat([dec,jan],ignore_index=True);write_csv(out/"predictions.csv",allp);write_csv(out/"metrics.csv",summarize(allp))
    day=jan[jan.horizon_hours>24];last=day[day.valid_time>=local_date("2026-01-31",config())]
    scores_jan=day.groupby("model").error.apply(lambda x:x.abs().mean()).sort_values()
    scores_day=last.groupby("model").error.apply(lambda x:x.abs().mean()).sort_values()
    report={"selected":selected,"december_mae":scores.to_dict(),"january_mae":scores_jan.to_dict(),"january31_mae":scores_day.to_dict(),
        "numerical_target_met_by": [m for m in scores_jan.index if scores_jan[m]<=.05 and scores_day[m]<=.05],"production_changed":False,"publication_provenance_verified":False}
    write_json(out/"report.json",report);print(report,flush=True)


if __name__=="__main__":main()
