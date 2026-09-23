"""Issuance-safe telemetry correction with a two-hour reporting delay."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from .accuracy import curves, curve_predict, observed_before, training_pairs, target_window, summarize
from .common import config, local_date, write_csv, write_json
from .data import load_hourly
from .weather_calibration import weather_table, inputs


def telemetry(hourly, weather, issues, delay=2):
    if delay < 0:
        raise ValueError("Negative reporting delay")
    records = []
    for issue in sorted(issues):
        # Forecast weather used for past-error estimation must itself have been
        # published by this issuance. Only matured measurements can be joined.
        known = weather[(weather.issued_at <= issue) & (weather.weather_available_at <= issue) &
                        (weather.valid_time >= issue-pd.Timedelta(hours=30)) & (weather.valid_time < issue)]
        known = known.sort_values("issued_at").drop_duplicates(["valid_time", "turbine_id"], keep="last")
        for turbine in config()["turbines"]:
            obs = hourly[hourly.complete & hourly.turbine_id.eq(turbine) &
                         (hourly.valid_time+pd.Timedelta(hours=1+delay) <= issue) &
                         (hourly.valid_time >= issue-pd.Timedelta(hours=30))].sort_values("valid_time")
            row = {"issued_at": issue, "turbine_id": turbine, "last_power": np.nan, "last_wind": np.nan,
                   "telemetry_age": 999., "power6": np.nan, "power24": np.nan, "bias6": 0., "bias24": 0., "bias_count": 0}
            if not obs.empty:
                last = obs.iloc[-1]
                age = (issue-last.valid_time-pd.Timedelta(hours=1)).total_seconds()/3600
                row["telemetry_age"] = age
                if age <= delay+3:
                    row.update(last_power=float(last.power_normalized), last_wind=float(last.wind_speed))
                    matched = obs.merge(known[known.turbine_id.eq(turbine)][["valid_time", "wind_speed_100m"]],on="valid_time")
                    for window in [6,24]:
                        selected = obs[obs.valid_time >= issue-pd.Timedelta(hours=delay+window)]
                        row[f"power{window}"] = float(selected.power_normalized.mean())
                        paired = matched[matched.valid_time >= issue-pd.Timedelta(hours=delay+window)]
                        if len(paired) >= 3:
                            row[f"bias{window}"] = float((paired.wind_speed-paired.wind_speed_100m).median())
                            row["bias_count"] = max(row["bias_count"],len(paired))
            records.append(row)
    return pd.DataFrame(records)


def dynamic_inputs(frame):
    x = inputs(frame, physical=True)
    for col in ["last_power", "last_wind", "telemetry_age", "power6", "power24", "bias6", "bias24", "bias_count"]:
        x[col] = frame[col]
    return x


METHODS = ["curve", *[f"bias{window}_{weight}" for window in [6,24] for weight in [.25,.5,1.]],
           "dynamic_power4", "dynamic_power6", "dynamic_wind4", "dynamic_wind6", "dynamic_wind4_blend"]


def fit_predict(method, train, obs, test):
    base = curves(obs)
    baseline = curve_predict(base,test)
    if method == "curve":
        return baseline
    if method.startswith("bias"):
        window, weight = method.split("_")
        # Damped weather correction, decaying with lead. Limit extreme offsets.
        correction = test[window].clip(-4,4).to_numpy()*float(weight)*np.exp(-(test.horizon_hours.to_numpy()-1)/48)
        return curve_predict(base,test,np.maximum(0,test.wind_speed_100m.to_numpy()+correction))
    wind = "wind" in method
    model = CatBoostRegressor(iterations=300, depth=6 if "6" in method else 4, learning_rate=.03,
                             l2_leaf_reg=10, loss_function="MAE", random_seed=42, thread_count=4,
                             verbose=False, allow_writing_files=False)
    model.fit(dynamic_inputs(train), train.wind_speed if wind else train.power_normalized, cat_features=["turbine_id"])
    p = model.predict(dynamic_inputs(test))
    if wind:
        p = curve_predict(base,test,np.maximum(0,p))
    if method.endswith("blend"):
        p = .5*p+.5*baseline
    stale = test.telemetry_age.gt(5).to_numpy()
    p[stale] = baseline[stale]
    return np.clip(p,0,1)


def evaluate(hourly, weather, state, start, end, methods):
    cutoff = local_date(start,config())
    obs = observed_before(hourly,cutoff)
    train = training_pairs(hourly,weather,cutoff).merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    test = target_window(weather,hourly,cutoff,local_date(end,config()))
    test = test[test.issued_at>=cutoff].merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    results=[]
    for method in methods:
        p=fit_predict(method,train,obs,test)
        part=test.copy();part["prediction"]=np.clip(p,0,1);part["error"]=part.prediction-part.actual
        part["model"]=method;part["fold"]=start[:7];results.append(part)
        print(start,method,part[part.horizon_hours>24].error.abs().mean(),flush=True)
    return pd.concat(results,ignore_index=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default="outputs/live-bias");args=parser.parse_args()
    out=Path(args.output)
    if out.exists(): raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    h,w=load_hourly(config()),weather_table()
    state=telemetry(h,w,w.issued_at.unique())
    write_csv(out/"telemetry.csv",state)
    write_json(out/"protocol.json",{"reporting_delay_hours_after_interval_end":2,"selection":"December day-ahead MAE","methods":METHODS,"january":"past available telemetry only; no January fitting"})
    dec=evaluate(h,w,state,"2025-12-01","2026-01-01",METHODS)
    ranking=dec[dec.horizon_hours>24].groupby("model").error.apply(lambda e:e.abs().mean()).sort_values()
    selected=ranking.index[0]
    write_json(out/"selection.json",{"selected":selected,"december_mae":ranking.to_dict()})
    jan=evaluate(h,w,state,"2026-01-01","2026-02-01",list(dict.fromkeys(["curve",selected])))
    combined=pd.concat([dec,jan],ignore_index=True);write_csv(out/"predictions.csv",combined);write_csv(out/"metrics.csv",summarize(combined))
    day=jan[jan.horizon_hours>24];last=day[day.valid_time>=local_date("2026-01-31",config())]
    report={"selected":selected,"december_mae":ranking.to_dict(),"january_mae":day.groupby("model").error.apply(lambda e:e.abs().mean()).to_dict(),"january31_mae":last.groupby("model").error.apply(lambda e:e.abs().mean()).to_dict(),"production_changed":False}
    write_json(out/"report.json",report);print(report,flush=True)


if __name__=="__main__": main()
