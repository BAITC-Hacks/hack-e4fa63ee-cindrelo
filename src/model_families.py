"""Fixed cross-family benchmark; December selection precedes January diagnostics."""
import argparse
import hashlib
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.kernel_approximation import Nystroem
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, SplineTransformer
from sklearn.pipeline import make_pipeline
from threadpoolctl import threadpool_limits

from .accuracy import observed_before, training_pairs, target_window, curves, curve_predict, summarize, uncertainty
from .common import config, local_date, write_csv, write_json
from .data import load_hourly
from .live_bias import telemetry, dynamic_inputs
from .weather_calibration import weather_table, inputs

FAMILIES = ["random_forest", "extra_trees", "hist_mae", "hist_mse", "knn", "spline", "rbf", "neural"]
METHODS = ["curve"] + [f"{family}_{mode}" for family in FAMILIES for mode in ["weather", "live"]]


def blend_analysis(source, output):
    """Post-hoc fixed blends; explicitly not independent test validation."""
    out=Path(output)
    if out.exists(): raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    p=pd.read_csv(Path(source)/"predictions.csv")
    for col in ["valid_time","issued_at"]: p[col]=pd.to_datetime(p[col],utc=True)
    keys=["fold","issued_at","valid_time","turbine_id"]
    base=p[p.model.eq("curve")]
    results=[base.copy()]
    for name,part in p[~p.model.eq("curve")].groupby("model"):
        merged=part.merge(base[keys+["prediction"]],on=keys,validate="one_to_one",suffixes=("","_base"))
        if len(merged)!=len(base): raise ValueError("Unequal blend coverage")
        merged["prediction"]=(merged.prediction+merged.prediction_base)/2
        merged["error"]=merged.prediction-merged.actual
        merged["model"]="blend_"+name
        results.append(merged.drop(columns="prediction_base"))
    combined=pd.concat(results,ignore_index=True)
    dec=combined[(combined.fold=="2025-12")&(combined.horizon_hours>24)]
    ranking=dec.groupby("model").error.apply(lambda e:e.abs().mean()).sort_values()
    selected=ranking.index[0]
    write_json(out/"selection.json",{"selected":selected,"december_mae":ranking.to_dict(),"post_hoc":True,"weight":.5})
    jan=combined[(combined.fold=="2026-01")&(combined.horizon_hours>24)]
    last=jan[jan.valid_time>=local_date("2026-01-31",config())]
    report={"selected":selected,"december_mae":ranking.to_dict(),"january_mae":jan.groupby("model").error.apply(lambda e:e.abs().mean()).sort_values().to_dict(),
            "january31_mae":last.groupby("model").error.apply(lambda e:e.abs().mean()).sort_values().to_dict(),"post_hoc":True,"production_changed":False}
    write_csv(out/"metrics.csv",summarize(combined));write_csv(out/"predictions.csv",combined)
    write_json(out/"report.json",report);print(report,flush=True)


def estimator(family):
    if family == "random_forest":
        model = RandomForestRegressor(n_estimators=150,max_depth=14,min_samples_leaf=20,max_features=.8,n_jobs=4,random_state=42)
    elif family == "extra_trees":
        model = ExtraTreesRegressor(n_estimators=150,max_depth=18,min_samples_leaf=10,max_features=.8,n_jobs=4,random_state=42)
    elif family.startswith("hist_"):
        model = HistGradientBoostingRegressor(loss="absolute_error" if family=="hist_mae" else "squared_error",
                    max_iter=250,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=10,learning_rate=.05,early_stopping=False,random_state=42)
    elif family == "knn":
        model = KNeighborsRegressor(n_neighbors=80,weights="distance",n_jobs=4)
    elif family == "spline":
        model = make_pipeline(SplineTransformer(n_knots=5,degree=3),Ridge(alpha=100.))
    elif family == "rbf":
        model = make_pipeline(Nystroem(gamma=.03,n_components=200,random_state=42),Ridge(alpha=10.))
    elif family == "neural":
        model = MLPRegressor(hidden_layer_sizes=(64,32),alpha=1.,max_iter=150,early_stopping=False,
                             batch_size=256,learning_rate_init=.001,random_state=42)
    else:
        raise ValueError(f"Unknown family {family}")
    return make_pipeline(SimpleImputer(strategy="median",add_indicator=True),StandardScaler(),model)


def matrix(frame, live):
    x = dynamic_inputs(frame) if live else inputs(frame,physical=True)
    x["turbine_id"] = frame.turbine_id.map({"turbine_1":0.,"turbine_2":1.})
    return x


def evaluate(h,w,state,start,end,methods,out):
    cutoff=local_date(start,config())
    obs=observed_before(h,cutoff)
    train=training_pairs(h,w,cutoff).merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    test=target_window(w,h,cutoff,local_date(end,config()))
    test=test[test.issued_at>=cutoff].merge(state,on=["issued_at","turbine_id"],validate="many_to_one")
    baseline=curve_predict(curves(obs),test)
    results=[];notes={}
    for method in methods:
        if method=="curve":
            p=baseline
        else:
            family,mode=method.rsplit("_",1)
            model=estimator(family)
            with warnings.catch_warnings(record=True) as caught, threadpool_limits(limits=4):
                warnings.simplefilter("always")
                model.fit(matrix(train,mode=="live"),train.power_normalized)
                p=model.predict(matrix(test,mode=="live"))
            notes[method]=[str(item.message) for item in caught]
            if mode=="live": p[test.telemetry_age.gt(5).to_numpy()]=baseline[test.telemetry_age.gt(5).to_numpy()]
        if not np.isfinite(p).all(): raise ValueError(f"Nonfinite {method}")
        part=test.copy();part["prediction"]=np.clip(p,0,1);part["error"]=part.prediction-part.actual
        part["model"]=method;part["fold"]=start[:7];results.append(part)
        print(start,method,part[part.horizon_hours>24].error.abs().mean(),flush=True)
        write_json(out/f"warnings_{start}.json",notes)
    return pd.concat(results,ignore_index=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default="outputs/model-families")
    parser.add_argument("--blends-from",help="Run a separate, explicitly post-hoc fixed-blend analysis")
    args=parser.parse_args()
    if args.blends_from:
        blend_analysis(args.blends_from,args.output)
        return
    out=Path(args.output)
    if out.exists(): raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    h,w=load_hourly(config()),weather_table();state=telemetry(h,w,w.issued_at.unique())
    write_json(out/"protocol.json",{"methods":METHODS,"selection":"December day-ahead MAE",
               "january":"all recipes shown diagnostically; cannot change the frozen December selection",
               "telemetry_delay_hours":2,"random_validation_split":False,
               "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    dec=evaluate(h,w,state,"2025-12-01","2026-01-01",METHODS,out)
    rank=dec[dec.horizon_hours>24].groupby("model").error.apply(lambda e:e.abs().mean()).sort_values()
    selected=rank.index[0];write_json(out/"selection.json",{"selected":selected,"december_mae":rank.to_dict()})
    write_csv(out/"december_predictions.csv",dec)
    print("FROZEN SELECTION",selected,flush=True)
    jan=evaluate(h,w,state,"2026-01-01","2026-02-01",METHODS,out)
    combined=pd.concat([dec,jan],ignore_index=True);write_csv(out/"predictions.csv",combined);write_csv(out/"metrics.csv",summarize(combined))
    day=jan[jan.horizon_hours>24];last=day[day.valid_time>=local_date("2026-01-31",config())]
    report={"selected":selected,"december_mae":rank.to_dict(),"january_mae":day.groupby("model").error.apply(lambda e:e.abs().mean()).sort_values().to_dict(),
            "january31_mae":last.groupby("model").error.apply(lambda e:e.abs().mean()).sort_values().to_dict(),
            "selected_january_uncertainty":uncertainty(jan,selected),"production_changed":False}
    write_json(out/"report.json",report);print(report,flush=True)


if __name__=="__main__":main()
